from __future__ import annotations

"""Agent 本地落盘路径布局。

按 docs/node-agent.md 的目录建议，所有节点级文件都写到
`<state_dir>/nodes/<node_id>/` 之下。"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AgentPaths:
    """单个节点目录布局的具名集合。"""

    state_dir: Path
    node_id: str

    @property
    def node_dir(self) -> Path:
        """节点专属目录。"""
        return self.state_dir / "nodes" / self.node_id

    @property
    def identity_file(self) -> Path:
        """持久化的 agent 身份与世代信息。"""
        return self.node_dir / "identity.json"

    @property
    def desired_state_file(self) -> Path:
        """最近一次成功 Desired State 的本地副本。"""
        return self.node_dir / "desired-state.json"

    @property
    def rendered_dir(self) -> Path:
        """渲染输出目录（配置文件与镜像构建上下文的根）。"""
        return self.node_dir / "rendered"

    @property
    def snapshots_dir(self) -> Path:
        """RuntimeSnapshot 与 ReconciliationReport 历史归档目录。"""
        return self.node_dir / "snapshots"

    @property
    def metrics_file(self) -> Path:
        """reconcile 运行指标（次数 / 失败 / 时长 / 最近状态）。"""
        return self.node_dir / "metrics.json"

    @property
    def container_definitions_dir(self) -> Path:
        """已应用容器定义记录目录（字段级 diff reason 的数据源）。"""
        return self.node_dir / "containers"

    @property
    def secrets_dir(self) -> Path:
        """节点本地密钥目录（WG 私钥等）。不进渲染产物、不上报、不入 file plan。"""
        return self.node_dir / "secrets"

    @property
    def wireguard_node_key_file(self) -> Path:
        """节点级 WG 私钥文件（0600）。一节点一把，所有 peer 共用。"""
        return self.secrets_dir / "wireguard" / "node.key"

    def ensure(self) -> None:
        """创建全部子目录。"""
        for path in (self.node_dir, self.rendered_dir, self.snapshots_dir):
            path.mkdir(parents=True, exist_ok=True)
        # secrets 目录权限收紧到仅属主可访问。
        self.secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)


__all__ = ["AgentPaths"]
