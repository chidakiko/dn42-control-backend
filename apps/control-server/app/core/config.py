from __future__ import annotations

"""控制服务器静态配置项。

MVP 阶段仅维护少量启动期常量；后续接入 secrets / 多环境时再演进。
"""

import os
from dataclasses import dataclass
from pathlib import Path


# 仓库根目录（apps/control-server/app/core/config.py 向上 4 层）。
# 默认 SQLite DSN 锚定在此，避免 cwd 不同时 ``./control.db`` 落在
# ``apps/control-server/`` 等子目录里。
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_DATABASE_URL = f"sqlite+aiosqlite:///{(_REPO_ROOT / 'control.db').as_posix()}"


def _env_flag(name: str, default: bool) -> bool:
    """从环境变量解析布尔开关；未设置时返回默认值。"""

    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    """从环境变量解析浮点阈值;缺失或非法时回退默认值。"""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_cors_origins(
    raw: str | None, default: tuple[str, ...]
) -> tuple[str, ...]:
    """解析逗号分隔的 CORS 白名单。未设置时返回默认；空字符串表示关闭跨源。"""

    if raw is None:
        return default
    origins = tuple(item.strip() for item in raw.split(",") if item.strip())
    return origins


def _load_recovery_public_key(raw: str | None) -> str | None:
    """把 ``DN42_CONTROL_RECOVERY_PUBLIC_KEY`` 解析成 PEM 文本。

    取值既可以是内联 PEM（以 ``-----BEGIN`` 开头），也可以是 PEM 文件路径。
    空值表示未启用托管。
    """

    if not raw:
        return None
    if raw.lstrip().startswith("-----BEGIN"):
        return raw
    path = Path(raw)
    if not path.exists():
        raise FileNotFoundError(
            f"DN42_CONTROL_RECOVERY_PUBLIC_KEY points to a missing file: {raw}"
        )
    return path.read_text(encoding="ascii")


@dataclass(frozen=True)
class ControlServerConfig:
    """Control Server 启动期配置。

    Attributes:
        database_url: SQLAlchemy 异步 DSN。默认指向仓库根目录下的 ``control.db``，
            便于本地与 CI 直接跑；生产可设为 ``postgresql+asyncpg://...`` 或
            ``mysql+asyncmy://...``。
        enrollment_token: 全局 bootstrap 注册 token；agent ``register`` 时与
            ``enrollment_tokens`` 表中按节点签发的 token 并行接受。设为 ``None``
            可关闭全局 token，只允许表内按节点 token 注册。
        admin_token: Admin API 的 Bearer token。``None`` 表示 Admin API 整体
            fail-closed（一律 403），生产必须显式配置。
        seed_bootstrap_node: 是否在 DB 为空时播种内置 demo 节点；默认 ``False`` 表示
            启动即空库，节点数据应由导入 / provision 流程写入。
        bootstrap_node_id: 内置 demo 节点的 ``node_id``，仅在 ``seed_bootstrap_node`` 开启时使用。
        bootstrap_agent_token: 与 bootstrap 节点关联的初始 Bearer token，便于本地联调。
        recovery_public_key_pem: 离线托管"恢复公钥"的 PEM 文本。节点用它封装 WG
            私钥后上报；控制面只存密文、永不持有恢复私钥。``None`` 表示未启用
            托管——节点仍上报公钥做一致性校验，但不产生托管密文。
    """

    database_url: str = _DEFAULT_DATABASE_URL
    enrollment_token: str | None = "enroll-token"
    admin_token: str | None = None
    seed_bootstrap_node: bool = False
    bootstrap_node_id: str = "edge1"
    bootstrap_agent_token: str = "mvp-agent-token"
    recovery_public_key_pem: str | None = None
    # 浏览器管理面（apps/web）跨源直连本服务时需要的 CORS 白名单。默认放行本地
    # Vite dev server；生产把管理面的真实源（或 "*"）写进
    # ``DN42_CONTROL_CORS_ORIGINS``（逗号分隔）。
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    # 健康判定的失联阈值（秒）。``health_stale_after`` 内未上报的 ok 节点降为 stale；
    # 超过更长的 ``health_down_after`` 完全没上报则判为 down（宕机），覆盖任何已知状态。
    health_stale_after_seconds: float = 900.0
    health_down_after_seconds: float = 3600.0

    @classmethod
    def from_env(cls) -> "ControlServerConfig":
        """从环境变量构造配置；未设置项保留默认。"""

        # 空字符串显式表示"关闭全局 enrollment token"。
        enrollment_env = os.environ.get("DN42_CONTROL_ENROLLMENT_TOKEN")
        if enrollment_env is None:
            enrollment_token = cls.enrollment_token
        else:
            enrollment_token = enrollment_env or None

        return cls(
            database_url=os.environ.get("DN42_CONTROL_DATABASE_URL", cls.database_url),
            enrollment_token=enrollment_token,
            admin_token=os.environ.get("DN42_CONTROL_ADMIN_TOKEN") or None,
            seed_bootstrap_node=_env_flag(
                "DN42_CONTROL_SEED_BOOTSTRAP_NODE", cls.seed_bootstrap_node
            ),
            bootstrap_node_id=os.environ.get(
                "DN42_CONTROL_BOOTSTRAP_NODE_ID", cls.bootstrap_node_id
            ),
            bootstrap_agent_token=os.environ.get(
                "DN42_CONTROL_BOOTSTRAP_AGENT_TOKEN", cls.bootstrap_agent_token
            ),
            recovery_public_key_pem=_load_recovery_public_key(
                os.environ.get("DN42_CONTROL_RECOVERY_PUBLIC_KEY")
            ),
            cors_origins=_parse_cors_origins(
                os.environ.get("DN42_CONTROL_CORS_ORIGINS"), cls.cors_origins
            ),
            health_stale_after_seconds=_env_float(
                "DN42_CONTROL_HEALTH_STALE_AFTER", cls.health_stale_after_seconds
            ),
            health_down_after_seconds=_env_float(
                "DN42_CONTROL_HEALTH_DOWN_AFTER", cls.health_down_after_seconds
            ),
        )


__all__ = ["ControlServerConfig"]
