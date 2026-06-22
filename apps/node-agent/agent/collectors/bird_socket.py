from __future__ import annotations

"""直连 BIRD 控制 socket 采集——绕开 ``docker exec`` + ``birdc`` 子进程。

``birdc`` 本质只是 BIRD 控制 socket（``bird.ctl``）的瘦客户端：连上后写一行命令、
读回带 4 位状态码的文本。本模块把这套协议在 agent 内实现一遍，对外暴露一个与
:class:`agent.core.exec.ContainerExec` ``run()`` **同签名**的 :class:`BirdSocketExec`，
因此路由采集器（``build_routing_observer``）可以原样把它当成执行后端注入，
``parse_bird_routes`` 及下游一行不动。

为什么不直接拿结构化数据：主线 BIRD（2.x / 3.x）没有 JSON / 机器可读输出，socket
里流出的就是 ``birdc`` 同款文本。直连省掉的是两层壳——Docker Engine API ``exec_run``
（每次新建 exec 实例 + 把整张表 demux 进内存）与容器内 fork ``birdc`` 子进程；并且
可以**流式**读取，不必先把上万条路由的输出整块 buffer 起来。

协议要点（BIRD 远程控制协议）：

- 每个服务器行形如 ``<4 位码><分隔><文本>``，分隔为 ``-``（同码续行）或空格（该码末行）；
  或以单个空格开头的纯续行（逐字显示）。
- ``0xxx`` 为状态码（``0001`` 欢迎、``0000`` OK 等），``1xxx`` 为数据，``8xxx`` 运行期错误、
  ``9xxx`` 解析错误。一条命令的回复以 ``0xxx`` 末行（成功）或 ``8xxx`` / ``9xxx``（失败）收尾。
- ``birdc`` 显示时把每行的状态码剥掉。这里做同样的剥码，使输出与 ``birdc`` 逐字节一致。
"""

import logging
import socket
from collections.abc import Callable

from ..core.exec import ExecResult

logger = logging.getLogger(__name__)

# 默认 BIRD2 控制 socket 路径（容器内 ``/run/bird/bird.ctl`` 经 bind-mount 暴露给 agent）。
DEFAULT_BIRD_SOCKET_PATH = "/run/bird/bird.ctl"

_RECV_SIZE = 65536


def _is_code_line(line: str) -> bool:
    """行是否以 4 位状态码开头（``NNNN`` 后接分隔符或行即止）。"""

    return len(line) >= 4 and line[:4].isdigit()


def _strip_code(line: str) -> str:
    """剥掉状态码 / 续行前导空格，得到与 ``birdc`` 一致的逐字文本。

    - ``NNNN<分隔>text`` → ``text``（跳过 4 位码 + 1 分隔符；``\t`` 等缩进得以保留）。
    - `` text``（纯续行）→ ``text``（去掉单个前导空格）。
    """

    if _is_code_line(line):
        return line[5:] if len(line) > 5 else ""
    if line.startswith(" "):
        return line[1:]
    return line


class _ReplyReader:
    """逐行喂入服务器回复，攒出文本并识别终止 / 错误。

    终止判定按**码值**而非分隔符：``0xxx`` 末行（成功）或任意 ``8xxx`` / ``9xxx``（错误）
    收尾——``1xxx`` 数据块的末行虽也用空格分隔，但码值 ≥ 1000 故不会被误判为终止。
    """

    __slots__ = ("lines", "code", "error", "done", "eof")

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.code: int | None = None
        self.error: str | None = None
        self.done = False
        self.eof = False

    def feed_line(self, line: str) -> None:
        if _is_code_line(line):
            code = int(line[:4])
            sep = line[4] if len(line) > 4 else " "
            if (code < 1000 and sep == " ") or code >= 8000:
                self.code = code
                if code >= 8000:
                    self.error = _strip_code(line)
                self.done = True
                return
        self.lines.append(_strip_code(line))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class _Framing:
    """在一条已连接 socket 上按行切分、逐回复读取（跨 ``recv`` 维护残缓冲）。

    缓冲挂在实例上而非 ``read_reply`` 局部，故 greeting 与后续命令回复即便落在同一个
    ``recv`` 包里也不会丢字节——上一回复终止行之后的残留留给下一回复消费。
    """

    def __init__(self, sock: "_SocketLike", *, recv_size: int = _RECV_SIZE) -> None:
        self._sock = sock
        self._buf = b""
        self._recv_size = recv_size

    def read_reply(self) -> _ReplyReader:
        reader = _ReplyReader()
        while not reader.done:
            while b"\n" in self._buf and not reader.done:
                raw, self._buf = self._buf.split(b"\n", 1)
                reader.feed_line(raw.decode("utf-8", errors="replace"))
            if reader.done:
                break
            chunk = self._sock.recv(self._recv_size)
            if not chunk:  # 对端在回复终止前关闭连接
                reader.eof = True
                reader.done = True
                break
            self._buf += chunk
        return reader


class _SocketLike:
    """仅声明本模块用到的 socket 方法，便于单测注入假实现。"""

    def recv(self, size: int) -> bytes: ...  # pragma: no cover - 协议占位

    def sendall(self, data: bytes) -> None: ...  # pragma: no cover

    def close(self) -> None: ...  # pragma: no cover


SocketFactory = Callable[[], _SocketLike]


class BirdControlSocket:
    """BIRD 控制 socket 客户端：每次 :meth:`query` 开一条短连接执行一条命令。

    300s 级的采集节奏下，每命令新建 unix-socket 连接的开销可忽略，换来无需维护
    长连接 / 重连状态机的简单与健壮。``connect`` 可注入，单测喂脚本化的假 socket。
    """

    def __init__(
        self,
        path: str,
        *,
        timeout: float = 10.0,
        connect: SocketFactory | None = None,
    ) -> None:
        self._path = path
        self._timeout = timeout
        self._connect = connect or self._default_connect

    def _default_connect(self) -> _SocketLike:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        sock.connect(self._path)
        return sock  # type: ignore[return-value]

    def query(self, command: str) -> tuple[int, str, str]:
        """执行一条 ``birdc`` 命令（不含 ``birdc`` 本身），返回 (rc, stdout, stderr)。

        - rc=0：成功，stdout 为剥码后的文本（与 ``birdc`` 输出一致）。
        - rc=1：BIRD 回了 ``8xxx`` / ``9xxx`` 错误（如表名不存在），stderr 为错误文本。
        - OSError（连不上 / 超时 / 半路断开）由调用方 :class:`BirdSocketExec` 兜成 rc=-1。
        """

        sock = self._connect()
        try:
            framing = _Framing(sock)
            framing.read_reply()  # 读掉 greeting（0001 ... ready.）
            sock.sendall((command + "\n").encode("utf-8"))
            reply = framing.read_reply()
        finally:
            try:
                sock.close()
            except OSError:  # pragma: no cover - 关闭是 best-effort
                pass
        if reply.eof and reply.code is None:
            raise OSError("bird socket closed before reply terminated")
        if reply.error is not None:
            return (1, reply.text, reply.error)
        return (0, reply.text, "")


class BirdSocketExec:
    """:class:`~agent.core.exec.ContainerExec` 兼容后端：把 ``birdc`` argv 走控制 socket。

    路由采集器只用到 ``run()``；``put_file`` 不适用（socket 不写文件）故未实现。
    ``container`` 参数被忽略——目标 socket 在构造时固定，不按容器名路由。
    """

    def __init__(
        self,
        socket_path: str,
        *,
        timeout: float = 10.0,
        socket_factory: SocketFactory | None = None,
    ) -> None:
        self._path = socket_path
        self._client = BirdControlSocket(socket_path, timeout=timeout, connect=socket_factory)

    def run(self, container: str, argv: list[str]) -> ExecResult:
        # argv 形如 ["birdc", "show", "route", ...]；socket 命令是 birdc 之后的部分。
        args = argv[1:] if argv and argv[0] == "birdc" else list(argv)
        command = " ".join(args)
        try:
            return self._client.query(command)
        except OSError as exc:
            logger.warning("bird socket 查询失败 path=%s cmd=%r err=%s", self._path, command, exc)
            # rc=-1 与 docker 后端的命令失败同语义：runner 据此返回 None（采集失败）。
            return (-1, "", f"bird socket error: {exc}")

    def put_file(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        raise NotImplementedError("BirdSocketExec 不支持 put_file（socket 仅用于只读采集）")


__all__ = [
    "DEFAULT_BIRD_SOCKET_PATH",
    "BirdControlSocket",
    "BirdSocketExec",
]
