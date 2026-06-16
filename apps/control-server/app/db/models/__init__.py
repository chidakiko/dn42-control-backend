from __future__ import annotations

"""控制服务器 ORM 模型聚合。

所有模型都共享 ``Base.metadata``；Alembic / 单元测试通过这个模块拿到完整表清单。
"""

from .audit import AdminAuditLog
from .base import Base
from .dns import DnsZone
from .generation import Generation
from .node import AgentToken, EnrollmentToken, Node, PendingRegistration
from .node_status import NodeStatus, NodeStatusEvent
from .peering import BgpSession, Peering, WgInterface
from .routing import NodeRouting, NodeRoutingEvent

__all__ = [
    "AdminAuditLog",
    "AgentToken",
    "Base",
    "BgpSession",
    "DnsZone",
    "EnrollmentToken",
    "Generation",
    "Node",
    "NodeRouting",
    "NodeRoutingEvent",
    "NodeStatus",
    "NodeStatusEvent",
    "PendingRegistration",
    "Peering",
    "WgInterface",
]
