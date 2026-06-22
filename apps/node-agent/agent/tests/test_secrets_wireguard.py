from __future__ import annotations

"""节点级 WG 密钥生命周期 + apply 注入单测。"""

import stat
import sys

from dn42_common import (
    derive_wireguard_public_key,
    generate_recovery_keypair,
    unseal_with_recovery_key,
)
from dn42_schemas.testing import build_hkg1_example_state

from agent.core.paths import AgentPaths
from agent.secrets import (
    build_wireguard_key_report,
    ensure_node_private_key,
    push_wireguard_key_to_container,
)


def _paths(tmp_path):
    state = build_hkg1_example_state()
    paths = AgentPaths(state_dir=tmp_path, node_id=state.node.node_id)
    paths.ensure()
    return state, paths


def test_node_key_generated_once_and_persisted(tmp_path) -> None:
    _state, paths = _paths(tmp_path)
    first = ensure_node_private_key(paths)
    second = ensure_node_private_key(paths)
    assert first == second  # 幂等：第二次复用已落盘私钥

    key_file = paths.wireguard_node_key_file
    assert key_file.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_report_is_node_level_and_sealed(tmp_path) -> None:
    state, paths = _paths(tmp_path)
    private_pem, public_pem = generate_recovery_keypair()

    report = build_wireguard_key_report(
        state, paths, recovery_public_pem=public_pem.decode("ascii")
    )

    assert report is not None
    assert report.node_id == state.node.node_id
    # 上报公钥 = 本地节点私钥推导，天然证明持有性。
    local_priv = paths.wireguard_node_key_file.read_text().strip()
    assert derive_wireguard_public_key(local_priv) == report.public_key
    # 托管密文用恢复私钥解封即得原私钥。
    assert report.private_key_escrow is not None
    assert unseal_with_recovery_key(report.private_key_escrow, private_pem).decode() == local_priv


def test_report_without_recovery_key_skips_escrow(tmp_path) -> None:
    state, paths = _paths(tmp_path)
    report = build_wireguard_key_report(state, paths, recovery_public_pem=None)
    assert report is not None
    assert report.private_key_escrow is None


def test_push_key_uses_put_archive_without_leaking_key(tmp_path) -> None:
    state, paths = _paths(tmp_path)
    build_wireguard_key_report(state, paths, recovery_public_pem=None)  # 确保密钥落盘

    exec_calls: list[tuple[str, list[str]]] = []
    pushed: list[tuple[str, str, str, bytes, int]] = []

    class _RecordingExec:
        def run(self, container: str, argv: list[str]) -> tuple[int, str, str]:
            exec_calls.append((container, argv))
            return 0, "", ""

        def put_file(
            self, container: str, dest_dir: str, filename: str, data: bytes, *, mode: int = 0o600
        ) -> None:
            pushed.append((container, dest_dir, filename, data, mode))

    push_wireguard_key_to_container(
        state, paths, container="dn42-wg-gateway", container_exec=_RecordingExec()
    )

    # 不依赖容器内 exec（容器 restarting 时 exec 必败而 put_archive 可用）：
    # 锚定 /run、中间目录由 put_file 在 tar 内创建。
    assert exec_calls == []
    assert len(pushed) == 1
    container, dest_dir, filename, data, mode = pushed[0]
    assert (container, dest_dir, filename, mode) == (
        "dn42-wg-gateway",
        "/run",
        "dn42-control/secrets/node.key",
        0o600,
    )

    # 关键：私钥经 API 流传输,内容正确且绝不出现在任何命令 argv。
    secret = paths.wireguard_node_key_file.read_text().strip()
    assert data.decode("ascii").strip() == secret
    for _container, argv in exec_calls:
        assert all(secret not in token for token in argv)
