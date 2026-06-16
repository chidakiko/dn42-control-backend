from __future__ import annotations

"""节点级 WireGuard 私钥的本地生命周期 + 托管 + apply 时注入。

设计要点（与控制面"离线公钥托管 + 一节点一把私钥"方案对齐）：

- **一节点一把、本地生成本地持有**：节点只有一把 WG 私钥（所有 peer 共用），
  生成一次落 ``secrets/wireguard/node.key``（0600），公钥由它推导后上报。
  控制面永不持有 WG 私钥明文。
- **托管**：拿到控制面下发的恢复公钥后，用 RSA-OAEP 把私钥封装成密文随公钥
  一起上报；控制面只存密文。恢复公钥缺失时只上报公钥（仍做一致性校验）。
- **apply 注入**：私钥**不**写进持久渲染产物（``.conf`` 保留 ``secret://`` 占位符），
  只在 apply 时经 Docker API ``put_archive`` 推进 wg 容器的临时文件
  ``/run/dn42-control/secrets/node.key``，由 apply 脚本替换占位符喂给
  ``wg syncconf``。私钥经 API 流传输，不出现在任何命令 argv / 日志。
"""

import stat

from dn42_schemas import DesiredState, InterfaceKind, WireGuardKeyReport
from dn42_common import (
    derive_wireguard_public_key,
    generate_wireguard_keypair,
    seal_to_recovery_key,
)

from ..core.exec import ContainerExec
from ..core.logging import get_logger
from ..core.paths import AgentPaths

_LOGGER = get_logger("secrets.wireguard")

SECRET_REF_SCHEME = "secret://"

# wg 容器内的临时密钥文件（容器可写层，重启不丢、容器重建即清）。
# 推送锚定在 /run（任何 Linux 容器都有），中间目录由 put_file 以 0700 创建——
# 不依赖容器内 exec，因此对 created/restarting 状态的容器同样生效，
# 消除"启动脚本等密钥、推送等容器 running"的时序死锁。
_CONTAINER_SECRET_ANCHOR = "/run"
_CONTAINER_NODE_KEY_RELATIVE = "dn42-control/secrets/node.key"


def is_secret_ref(value: str | None) -> bool:
    """判断 ``private_key_ref`` 是否为需要本地兑现的 ``secret://`` 引用。"""

    return bool(value) and value.startswith(SECRET_REF_SCHEME)


def _has_secret_wireguard_interface(state: DesiredState) -> bool:
    return any(
        iface.kind == InterfaceKind.WIREGUARD and is_secret_ref(iface.private_key_ref)
        for iface in state.interfaces
    )


def ensure_node_private_key(paths: AgentPaths) -> str:
    """返回节点 WG 私钥（base64）；不存在则生成并 0600 落盘。"""

    key_file = paths.wireguard_node_key_file
    if key_file.exists():
        return key_file.read_text(encoding="ascii").strip()

    private_b64, _public_b64 = generate_wireguard_keypair()
    key_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_file.write_text(private_b64, encoding="ascii", newline="\n")
    key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    _LOGGER.info("secrets: 生成新的节点级 WG 私钥（本地持有）")
    return private_b64


def build_wireguard_key_report(
    state: DesiredState,
    paths: AgentPaths,
    *,
    recovery_public_pem: str | None,
) -> WireGuardKeyReport | None:
    """节点存在 secret:// 引用的 WG 接口时，构造节点级公钥 + 托管上报。

    没有需要兑现的 WG 接口时返回 ``None``（无需密钥）。恢复公钥为空时只上报公钥
    （``private_key_escrow=None``）。
    """

    if not _has_secret_wireguard_interface(state):
        return None

    private_b64 = ensure_node_private_key(paths)
    public_b64 = derive_wireguard_public_key(private_b64)
    escrow = (
        seal_to_recovery_key(private_b64.encode("ascii"), recovery_public_pem)
        if recovery_public_pem
        else None
    )
    return WireGuardKeyReport(
        node_id=state.node.node_id,
        public_key=public_b64,
        private_key_escrow=escrow,
    )


def push_wireguard_key_to_container(
    state: DesiredState,
    paths: AgentPaths,
    *,
    container: str,
    container_exec: ContainerExec,
) -> None:
    """把节点 WG 私钥推进 wg 容器的临时文件，供 apply 脚本注入。

    best-effort：任一步失败只记录，不让 reconcile 崩。私钥经 Docker API
    ``put_archive`` 流式传输（文件权限 0600 在 tar 条目里声明），不出现在
    任何命令 argv。
    """

    if not _has_secret_wireguard_interface(state):
        return
    key_file = paths.wireguard_node_key_file
    if not key_file.exists():
        return

    try:
        container_exec.put_file(
            container,
            _CONTAINER_SECRET_ANCHOR,
            _CONTAINER_NODE_KEY_RELATIVE,
            key_file.read_bytes(),
            mode=0o600,
        )
    except Exception as exc:  # noqa: BLE001 - 推送密钥是 best-effort
        _LOGGER.warning("secrets: 推送 node.key 进容器失败：%s", exc)


__all__ = [
    "SECRET_REF_SCHEME",
    "build_wireguard_key_report",
    "ensure_node_private_key",
    "is_secret_ref",
    "push_wireguard_key_to_container",
]
