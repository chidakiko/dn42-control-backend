from __future__ import annotations

"""控制服务器测试公共 fixture。

每条用例独占一份临时 SQLite 文件，避免互相污染。生产 lifespan **不再** seed
任何节点（空库），所以需要预置节点的用例在这里**显式** seed（见 ``_seed_helper``）。
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import ControlServerConfig
from app.main import create_app

from ._seed_helper import seed_test_db


@pytest.fixture
def config(tmp_path: Path) -> ControlServerConfig:
    return ControlServerConfig(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'control.db').as_posix()}",
        seed_bootstrap_node=True,
        admin_token="test-admin-token",
    )


@pytest.fixture
def client(config: ControlServerConfig) -> Iterator[TestClient]:
    # 生产路径不 seed；测试在这里显式预置 bootstrap 节点。
    if config.seed_bootstrap_node:
        seed_test_db(config)
    # 默认携带 admin Bearer：admin API 现在 fail-closed，绝大多数用例直接复用；
    # agent 面的鉴权用例显式覆盖 Authorization，不受默认头影响。
    app = create_app(config)
    with TestClient(
        app, headers={"Authorization": f"Bearer {config.admin_token}"}
    ) as client:
        yield client
