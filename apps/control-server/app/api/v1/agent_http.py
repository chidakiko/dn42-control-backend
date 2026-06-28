from __future__ import annotations

"""Agent HTTP 业务通道。

设计原则：
- `register` 不要求 Bearer，但要校验 enrollment_token：全局 bootstrap token
  （配置项，可关闭）或 `enrollment_tokens` 表中按节点签发的一次性 token。
- 注册发 token 前强制校验审批状态：rejected 一律 403（即使已 provision）；
  pending 不发 token；只有无审批记录（管理员直接 provision）或 approved 才放行。
- 其它端点一律要求 Bearer，且只允许 agent 操作自己的 node。
- 响应体保持与 `docs/api.md` 兼容（node-agent 已按此契约写代码）。
"""

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from dn42_common import agent_id_for, recovery_key_fingerprint
from dn42_schemas import (
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    ApplyResult,
    BootstrapStatus,
    RecoveryPublicKeyResponse,
    ReconciliationReport,
    RoutingTableSnapshot,
    RuntimeSnapshot,
    WireGuardKeyReport,
    WireGuardKeyReportResult,
    WireGuardReresolveReport,
    WireGuardTrafficSample,
)

from ...core.config import ControlServerConfig
from ...core.events import EventBus
from ...db.engine import Database
from ...schemas.events import DesiredStateUpdatedEvent
from ...services.desired_state import DesiredStateStore
from ...services.enrollment import EnrollmentGrant, EnrollmentTokenStore
from ...services.node_status import NodeStatusStore
from ...services.pending_registrations import PendingRegistrationStore
from ...services.routing import RoutingStore
from ...services.tokens import TokenPrincipal, TokenStore, hash_token
from ...services.traffic import TrafficStore
from ...services.wireguard_keys import STATUS_UNKNOWN_NODE, apply_wireguard_key_report
from ..deps import (
    get_config,
    get_database,
    get_desired_state,
    get_enrollment_tokens,
    get_event_bus,
    get_node_status,
    get_pending_registrations,
    get_routing,
    get_tokens,
    get_traffic,
    require_agent,
)

router = APIRouter(prefix="/agent", tags=["agent"])

_LOGGER = logging.getLogger("dn42.control.agent")


def _ensure_self(principal: TokenPrincipal, target_node_id: str) -> None:
    if principal.node_id != target_node_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"token bound to node {principal.node_id}, payload references {target_node_id}",
        )


def _matches_global_enrollment(candidate: str, configured: str | None) -> bool:
    if configured is None:
        return False
    return hmac.compare_digest(hash_token(candidate), hash_token(configured))


@router.post("/register", response_model=AgentRegistrationResponse)
async def register_agent(
    request: AgentRegistrationRequest,
    config: ControlServerConfig = Depends(get_config),
    tokens: TokenStore = Depends(get_tokens),
    desired_state: DesiredStateStore = Depends(get_desired_state),
    pending: PendingRegistrationStore = Depends(get_pending_registrations),
    enrollment: EnrollmentTokenStore = Depends(get_enrollment_tokens),
) -> AgentRegistrationResponse:
    requested = request.requested_node_id

    # 1) enrollment 门票：全局 bootstrap token（不绑定节点、不消费）或
    #    enrollment_tokens 表中的一次性 token（可绑定节点）。
    grant: EnrollmentGrant | None = None
    if not _matches_global_enrollment(request.enrollment_token, config.enrollment_token):
        grant = await enrollment.resolve(request.enrollment_token)
        if grant is None:
            _LOGGER.warning("register rejected: invalid enrollment token (requested node=%s)", requested)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid enrollment token",
            )
        if grant.node_id is not None and grant.node_id != requested:
            _LOGGER.warning(
                "register rejected: enrollment token bound to node=%s used for node=%s",
                grant.node_id,
                requested,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="enrollment token not valid for requested node",
            )

    # 2) 审批门禁：rejected 节点显式拒绝，即使已 provision。
    reg_status = await pending.status_for(requested)
    if reg_status == "rejected":
        _LOGGER.warning("register rejected: node=%s was rejected by admin", requested)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"registration for node {requested} was rejected by admin",
        )

    known = await desired_state.known_node_ids()

    if requested not in known:
        # 未知节点：登记待审批记录（已 approved 等待 provision 的不重新入队）。
        if reg_status in (None, "pending"):
            await pending.record(
                requested,
                hostname=request.inventory.hostname,
                inventory=request.inventory.model_dump(mode="json"),
            )
            message = "node not provisioned; registration pending admin approval"
        else:
            message = "registration approved; awaiting admin provision"
        return AgentRegistrationResponse(
            status=BootstrapStatus.PENDING_APPROVAL,
            node_id=requested,
            agent_id=agent_id_for(requested),
            agent_token=None,
            desired_state_generation=None,
            message=message,
        )

    if reg_status == "pending":
        # 已 provision 但审批仍在 pending：发 token 前必须等管理员表态。
        return AgentRegistrationResponse(
            status=BootstrapStatus.PENDING_APPROVAL,
            node_id=requested,
            agent_id=agent_id_for(requested),
            agent_token=None,
            desired_state_generation=None,
            message="node provisioned but registration awaiting admin approval",
        )

    # 节点已建于 Node 表但还没物化出第一代（current_generation==0，例如管理员
    # 直接 POST /nodes 后还没加接口/未 provision）：没有可下发的 DesiredState，
    # 不能发 token（ACCEPTED 要求 generation≥1，否则 schema 校验抛 500）。回
    # PENDING_APPROVAL 让 agent 等控制面发布第一代后再来。
    state = await desired_state.get(requested)
    if state is None:
        return AgentRegistrationResponse(
            status=BootstrapStatus.PENDING_APPROVAL,
            node_id=requested,
            agent_id=agent_id_for(requested),
            agent_token=None,
            desired_state_generation=None,
            message="node exists but no desired state has been published yet",
        )

    if config.seed_bootstrap_node and requested == config.bootstrap_node_id:
        # 本地联调：demo 节点复用配置中的固定 token，便于直接照文档示例调用。
        token = await tokens.issue(requested, token=config.bootstrap_agent_token)
    else:
        token = await tokens.issue(requested)

    if grant is not None:
        # 表内门票是一次性的：成功换取 agent token 后立即消费。
        await enrollment.mark_used(grant.token_id)

    return AgentRegistrationResponse(
        status=BootstrapStatus.ACCEPTED,
        node_id=requested,
        agent_id=agent_id_for(requested),
        agent_token=token,
        desired_state_generation=state.generation,
    )


@router.get("/desired-state")
async def get_agent_desired_state(
    principal: TokenPrincipal = Depends(require_agent),
    desired_state: DesiredStateStore = Depends(get_desired_state),
) -> dict:
    state = await desired_state.get(principal.node_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no desired state for node {principal.node_id}",
        )
    return state.model_dump(mode="json")


@router.get("/recovery-public-key", response_model=RecoveryPublicKeyResponse)
async def get_recovery_public_key(
    principal: TokenPrincipal = Depends(require_agent),
    config: ControlServerConfig = Depends(get_config),
) -> RecoveryPublicKeyResponse:
    """分发离线托管的恢复公钥（PEM）+ 指纹。

    公钥非秘密；真实性靠 TLS 保证。未配置时 ``configured=False``，节点据此跳过
    托管、只上报公钥做一致性校验。
    """

    pem = config.recovery_public_key_pem
    if pem is None:
        return RecoveryPublicKeyResponse(configured=False)
    return RecoveryPublicKeyResponse(
        configured=True,
        public_key_pem=pem,
        fingerprint=recovery_key_fingerprint(pem),
    )


@router.post("/wireguard-keys", response_model=WireGuardKeyReportResult)
async def post_wireguard_keys(
    report: WireGuardKeyReport,
    principal: TokenPrincipal = Depends(require_agent),
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> WireGuardKeyReportResult:
    """登记节点 WG 公钥 + 托管密文，执行严格一致性校验并按需向对端传播。

    公钥与记录不符 → 409、事务回滚，节点不得用偏离密钥拉隧道。首次登记会把公钥
    回填进所有"对端是本节点"的接口并重新物化这些对端节点，提交后广播。
    """

    _ensure_self(principal, report.node_id)

    async with db.session() as session:
        outcome = await apply_wireguard_key_report(
            session, report.node_id, report.public_key, report.private_key_escrow
        )
        if outcome.rejected:
            # 公钥漂移是安全敏感事件：记录以便排查节点身份冲突 / 投毒尝试。
            _LOGGER.warning(
                "wireguard key conflict for node=%s: %s", report.node_id, outcome.detail
            )
            # 事务内抛出 → 回滚，不留半成品。
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "wireguard public key conflicts with the recorded key",
                    "node_id": report.node_id,
                    "detail": outcome.detail,
                },
            )
        if outcome.status == STATUS_UNKNOWN_NODE:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=outcome.detail)

    # 提交后再广播：避免"事件先发、事务后回滚"导致对端拉到尚未落库的世代。
    for sibling, generation in outcome.propagated.items():
        event = DesiredStateUpdatedEvent(
            generation=generation, reason=f"peer {report.node_id} wireguard key"
        )
        await bus.publish(sibling, event.model_dump(mode="json"))

    return WireGuardKeyReportResult(
        node_id=report.node_id,
        accepted=True,
        status=outcome.status,
        detail=outcome.detail,
        propagated_to=sorted(outcome.propagated),
    )


@router.post("/runtime-snapshot")
async def post_runtime_snapshot(
    snapshot: RuntimeSnapshot,
    principal: TokenPrincipal = Depends(require_agent),
    node_status: NodeStatusStore = Depends(get_node_status),
) -> dict:
    _ensure_self(principal, snapshot.node_id)
    await node_status.record_snapshot(snapshot)
    return {
        "accepted": True,
        "node_id": snapshot.node_id,
        "generation": snapshot.generation,
        "containers": len(snapshot.containers),
        "interfaces": len(snapshot.interfaces),
    }


@router.post("/routing-table")
async def post_routing_table(
    snapshot: RoutingTableSnapshot,
    principal: TokenPrincipal = Depends(require_agent),
    routing: RoutingStore = Depends(get_routing),
) -> dict:
    """接收 agent 周期上报的 BIRD 路由全表（独立于 reconcile 的观测）。"""

    _ensure_self(principal, snapshot.node_id)
    await routing.record_snapshot(snapshot)
    return {
        "accepted": True,
        "node_id": snapshot.node_id,
        "observation": snapshot.observation.value,
        "routes": len(snapshot.routes),
    }


@router.post("/wireguard-traffic")
async def post_wireguard_traffic(
    sample: WireGuardTrafficSample,
    principal: TokenPrincipal = Depends(require_agent),
    traffic: TrafficStore = Depends(get_traffic),
) -> dict:
    """接收 agent 30s 轻量 WG 流量采样（全 peer 累计收 / 发字节之和）。

    独立于 reconcile 的高频观测：压入 Redis 热窗口 + 5min 降采样存档，供 ``/traffic``
    画 30s 粒度吞吐曲线。绝不参与对账 / apply。
    """

    _ensure_self(principal, sample.node_id)
    await traffic.record_sample(sample)
    return {
        "accepted": True,
        "node_id": sample.node_id,
        "rx_bytes": sample.rx_bytes,
        "tx_bytes": sample.tx_bytes,
        "peer_count": sample.peer_count,
    }


@router.post("/wireguard-reresolve")
async def post_wireguard_reresolve(
    report: WireGuardReresolveReport,
    principal: TokenPrincipal = Depends(require_agent),
    node_status: NodeStatusStore = Depends(get_node_status),
) -> dict:
    """接收 agent 周期上报的 WG endpoint 重解析结果（自愈观测，独立于 reconcile）。

    纯信息性：只 append 历史事件，不参与健康派生 / 对账。
    """

    _ensure_self(principal, report.node_id)
    await node_status.record_reresolve(report)
    return {
        "accepted": True,
        "node_id": report.node_id,
        "checked": report.checked,
        "reresolved": len(report.reresolved),
    }


@router.post("/reconciliation-report")
async def post_reconciliation_report(
    report: ReconciliationReport,
    principal: TokenPrincipal = Depends(require_agent),
    node_status: NodeStatusStore = Depends(get_node_status),
) -> dict:
    _ensure_self(principal, report.node_id)
    await node_status.record_report(report)
    return {
        "accepted": True,
        "node_id": report.node_id,
        "status": report.status,
        "drift_items": len(report.drift),
    }


@router.post("/apply-result")
async def post_apply_result(
    result: ApplyResult,
    principal: TokenPrincipal = Depends(require_agent),
    node_status: NodeStatusStore = Depends(get_node_status),
) -> dict:
    _ensure_self(principal, result.node_id)
    await node_status.record_apply(result)
    return {
        "accepted": True,
        "node_id": result.node_id,
        "generation": result.generation,
        "status": result.status,
    }


__all__ = ["router"]
