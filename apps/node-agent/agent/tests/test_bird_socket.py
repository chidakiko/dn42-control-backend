from __future__ import annotations

"""BIRD 控制 socket 客户端的协议分帧 / 剥码测试。

不碰真实 socket：注入脚本化的假 socket，覆盖剥码、tab 属性行保留、greeting 消费、
``0000`` 终止、``8xxx`` 错误、半路 EOF，以及 BirdSocketExec 的 argv 翻译与回落语义。
"""

from agent.collectors.bird_socket import BirdControlSocket, BirdSocketExec
from agent.collectors.routing import parse_bird_routes

GREETING = b"0001 BIRD 2.0.12 ready.\n"


def _coded(text: str, *, code: str = "1007") -> bytes:
    """把 birdc 风格文本逐行加上数据状态码 + ``0000`` 末行，模拟 socket 原始回复。

    数据码取值不影响解析（任意 ``>=1000`` 都只被剥掉），故统一用一个码即可。
    """

    lines = [f"{code}-{ln}" for ln in text.splitlines()]
    lines.append("0000 ")
    return ("\n".join(lines) + "\n").encode("utf-8")


class _ChunkSocket:
    """按预置字节块逐次 ``recv``，``sendall`` 仅记录；用于精确控制分包边界。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.sent: list[bytes] = []
        self.closed = False

    def recv(self, _size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


class _ScriptedBird:
    """命令感知的假 BIRD：先吐 greeting，``sendall`` 后按命令排入对应回复。"""

    def __init__(self, replies: dict[str, bytes]) -> None:
        self._replies = replies
        self._queue: list[bytes] = [GREETING]
        self.sent: list[str] = []

    def recv(self, _size: int) -> bytes:
        return self._queue.pop(0) if self._queue else b""

    def sendall(self, data: bytes) -> None:
        command = data.decode("utf-8").strip()
        self.sent.append(command)
        self._queue.append(self._replies.get(command, b"8001 No such command.\n"))

    def close(self) -> None:  # pragma: no cover - 无资源
        pass


_ROUTES_V4 = (
    "Table master4:\n"
    "172.20.0.0/24        unicast [peer1 2024-06-01 10:00:00] * (100) [AS4242420000i]\n"
    "\tvia 172.20.0.1 on eth0\n"
    "\tType: BGP univ\n"
    "\tBGP.as_path: 4242420000\n"
    "fd00::/8             unicast [peer1 2024-06-01 10:00:00] * (100) [AS4242420001i]\n"
    "\tvia 172.20.0.1 on eth0\n"
    "\tBGP.as_path: 4242420001\n"
)


def test_query_strips_codes_to_birdc_equivalent_text() -> None:
    raw = _coded(_ROUTES_V4)
    client = BirdControlSocket(
        "/x", connect=lambda: _ScriptedBird({"show route table master4 all": raw})
    )
    rc, out, err = client.query("show route table master4 all")

    assert rc == 0
    assert err == ""
    # 关键不变量：socket 剥码后的解析结果与直接喂 birdc 文本完全一致。
    assert parse_bird_routes(out) == parse_bird_routes(_ROUTES_V4)


def test_query_preserves_tab_indented_attribute_lines() -> None:
    raw = _coded(_ROUTES_V4)
    client = BirdControlSocket("/x", connect=lambda: _ScriptedBird({"q": raw}))
    _, out, _ = client.query("q")
    # 属性行的前导 \t 必须保留（剥码只去 5 字符码前缀，不动正文缩进）。
    assert "\tvia 172.20.0.1 on eth0" in out
    assert "\tBGP.as_path: 4242420000" in out


def test_query_handles_leading_space_continuation_lines() -> None:
    # 纯续行（行首单空格、无状态码）应只去掉一个前导空格。
    raw = b"1007-header line\n continuation text\n0000 \n"
    client = BirdControlSocket("/x", connect=lambda: _ChunkSocket([GREETING, raw]))
    _, out, _ = client.query("q")
    assert out == "header line\ncontinuation text"


def test_query_consumes_greeting_then_returns_reply() -> None:
    bird = _ScriptedBird({"show route table master4 all": _coded(_ROUTES_V4)})
    client = BirdControlSocket("/x", connect=lambda: bird)
    rc, out, _ = client.query("show route table master4 all")
    assert rc == 0
    assert "172.20.0.0/24" in out
    assert bird.sent == ["show route table master4 all"]  # 命令已发出
    assert "BIRD 2.0.12 ready" not in out  # greeting 不混入输出


def test_query_when_greeting_and_reply_share_one_recv_chunk() -> None:
    # greeting 与回复落在同一个 recv 包：跨回复的残缓冲不能丢字节。
    combined = GREETING + _coded(_ROUTES_V4)
    client = BirdControlSocket("/x", connect=lambda: _ChunkSocket([combined]))
    rc, out, _ = client.query("q")
    assert rc == 0
    assert parse_bird_routes(out) == parse_bird_routes(_ROUTES_V4)


def test_query_splits_reply_across_multiple_recv_chunks() -> None:
    raw = _coded(_ROUTES_V4)
    mid = len(raw) // 2
    client = BirdControlSocket("/x", connect=lambda: _ChunkSocket([GREETING, raw[:mid], raw[mid:]]))
    rc, out, _ = client.query("q")
    assert rc == 0
    assert parse_bird_routes(out) == parse_bird_routes(_ROUTES_V4)


def test_query_maps_bird_error_reply_to_rc1() -> None:
    raw = b"8002 Network not in table\n"
    client = BirdControlSocket("/x", connect=lambda: _ChunkSocket([GREETING, raw]))
    rc, out, err = client.query("show route table nope")
    assert rc == 1
    assert out == ""
    assert "Network not in table" in err


def test_query_raises_on_eof_before_terminator() -> None:
    # greeting 后对端直接关闭，回复未终止 → OSError（上层兜成 rc=-1）。
    client = BirdControlSocket("/x", connect=lambda: _ChunkSocket([GREETING]))
    try:
        client.query("q")
    except OSError:
        pass
    else:  # pragma: no cover - 断言失败路径
        raise AssertionError("expected OSError on premature EOF")


def test_socket_exec_translates_argv_and_drops_birdc() -> None:
    bird = _ScriptedBird({"show route table master4 all": _coded(_ROUTES_V4)})
    exec_ = BirdSocketExec("/x", socket_factory=lambda: bird)
    rc, out, err = exec_.run("bird-1", ["birdc", "show", "route", "table", "master4", "all"])
    assert rc == 0
    assert bird.sent == ["show route table master4 all"]  # birdc 前缀已剥离
    assert parse_bird_routes(out) == parse_bird_routes(_ROUTES_V4)


def test_socket_exec_returns_rc_minus1_on_transport_failure() -> None:
    def boom() -> object:
        raise OSError("connection refused")

    exec_ = BirdSocketExec("/x", socket_factory=boom)
    rc, out, err = exec_.run("bird-1", ["birdc", "show", "protocols"])
    assert rc == -1
    assert out == ""
    assert "bird socket error" in err
