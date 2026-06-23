# 升级与数据库迁移

本文讲三件事：怎么升级 Node Agent（pip wheel 滚动）、怎么升级 Control Server、怎么跑数据库迁移（Alembic）。脚本参数见 [../reference/cli-and-scripts.md](../reference/cli-and-scripts.md)，迁移清单见 [../reference/database.md](../reference/database.md#迁移alembic)。

## Node Agent 升级（pip wheel）

取代"手动 scp 覆盖 venv 单文件 + 手清 `__pycache__`"的旧做法（易漏文件、留字节码、版本飘移）。现在统一走标准 pip wheel。

### 构建

```bash
bash deploy/build_wheels.sh
```

- 构建 5 个一方 wheel 到 `dist/`：`dn42-common` / `dn42-schemas` / `dn42-runtime` / `dn42-templates` / `dn42-node-agent`（模板数据文件随包打入）。
- 版本 = `1.0.<git commit 数>`（如 `1.0.107`），单调递增，供 `pip -U` 识别。
- 版本注入：构建时临时改各 `pyproject.toml` 的 `[project].version`，构建完即还原（trap 兜底）。不用 hatch-vcs——`.dockerignore` 排除了 `.git`，dynamic version 会让控制面 docker 构建拿不到 git 历史而失败。
- **control-server 不在此列**：它走 `docker build`（从源码全新重建），无漏文件/残留问题。

### 滚动升级一个节点

```bash
bash deploy/agent_pip_rollout.sh <ssh-target> <key-path> [ssh-port]
```

脚本做三件事：

1. `scp dist/*.whl` 到节点本地 `/opt/dn42-wheels`（私有仓库 = pip `--find-links` 目录）。
2. `pip install -U --no-index --find-links /opt/dn42-wheels/ dn42-common dn42-schemas dn42-runtime dn42-templates dn42-node-agent`：
   - `--no-index` 全程离线，不碰 PyPI/镜像源（规避中国/香港镜像源的已知问题）。
   - **显式列全 5 个包**：pip 默认 `only-if-needed` 不会主动升无版本约束的依赖，必须逐个点名。
3. 重启 `dn42-node-agent.service` 并回显 `pip list | grep dn42`。

为什么用"节点本地 find-links"而非中央 HTTP index：小 fleet 下本地 `/opt/dn42-wheels` + `--find-links` 零公网暴露、零常驻服务、离线，分发由 rollout 脚本顺带完成。

### 控制面 / agent 锁步升级

当一次变更同时改了 agent 行为与控制面协议/校验（如新增字段），**先升 agent、后升控制面**（agent 端自填默认值，控制面后跟）；反之可能让旧 agent 上报被新校验拒。历史能力（import-limit、prefilter、RPKI not-found 等）都按此锁步滚动，对应的一次性 `deploy/agent_*_rollout.sh` 已被 wheel 机制取代（见 [../reference/cli-and-scripts.md](../reference/cli-and-scripts.md#历史一次性-rollout已被-wheel-升级取代留档)）。

### 回退

节点 `/opt/dn42-wheels` 保留历次 wheel；回退就用旧版本号那批重跑 `pip install -U --no-index --find-links ...`，或 `git checkout <旧commit>` 后重新 `build_wheels.sh` + rollout。

### 验证

```bash
# 控制面看 fleet 上报
curl -s -H "Authorization: Bearer <admin-token>" http://127.0.0.1:8000/api/v1/admin/health
# 节点看版本一致
/opt/dn42-agent/venv/bin/pip list | grep dn42
```

`last_report_status=succeeded` + 各节点 `dn42-*` 同一版本号 = 升级成功。

## Control Server 升级

control-server 走 docker 全新重建（或 systemd 下重装 venv + 重启）。升级步骤：

1. 拉新代码。
2. **先跑数据库迁移**（见下）：`alembic upgrade head`。
3. 重建/重启 control-server。

注意迁移与代码的顺序：删列类迁移（如 `drop_rpki_unknown`、`drop_node_routing_routes`）要在新代码不再引用旧列后再跑；加列类要在新代码前跑。

## 数据库迁移（Alembic）

- 后端库是 **PostgreSQL**（ASN 等列用 `BigInteger`——DN42 ASN 超 int32）。
- **启动建表**：control-server 启动 `Base.metadata.create_all` 建缺失表（**不改已存在表**）；现已与 Alembic 等价——alembic 链补全后可从空库一路 `upgrade head` 跑通，产出与 `create_all` 逐表逐列一致。
- **规范迁移**：`migrations/` 与 control-server 共享 `DN42_CONTROL_DATABASE_URL`（`migrations/env.py`）。

```bash
export DN42_CONTROL_DATABASE_URL=postgresql+asyncpg://user:pass@host/db
alembic upgrade head        # 升到最新
alembic current             # 看当前版本
alembic history             # 看迁移链
alembic downgrade -1        # 回退一步（注意删列类多为不可逆）
```

迁移清单与各自作用见 [../reference/database.md](../reference/database.md#迁移alembic)。

### `create_all` 库的坑（历史）

若某控制面历史上用 `create_all` 起库（没走 alembic），跨 schema 升级时 `create_all` **不会**改既有表，新迁移里的"删列/改列"也不会自动应用——需要手动 `ALTER TABLE ... DROP COLUMN ...`（如 `rpki_unknown`），或 `alembic stamp` 对齐版本后再 `upgrade`。

> 该坑主要存在于早期 SQLite + create_all 起库期。现在 alembic 链已修到从空库可跑通且与 create_all 完全等价，新部署用 docker 全栈方案（PostgreSQL）+ `create_all`/`alembic upgrade head` 任一即可，schema 一致。SQLite→PostgreSQL 整库迁移用 `docker/migrate_sqlite_to_postgres.py`。

### `FOR UPDATE` + lazy-joined 关系的坑（SQLite→PostgreSQL）

`Node.dns_group` 是 `lazy="joined"` 的**可空**关系，所以 `session.get(Node, …, with_for_update=True)` 会发 `SELECT … FROM nodes LEFT OUTER JOIN dns_groups … FOR UPDATE`。SQLite 直接忽略 `FOR UPDATE` 子句，所以一直没暴露；切到 **PostgreSQL 后** asyncpg 报 `FeatureNotSupportedError: FOR UPDATE cannot be applied to the nullable side of an outer join`，导致**所有 materialize / 世代回滚 / agent WG 公钥上报全部 500**（provision、改接口、改拓扑、注册都被卡死）。

修复：行级锁限定只锁 `nodes` 主表——`with_for_update={"of": Node}`（生成 `FOR UPDATE OF nodes`），不去锁外连接的可空侧。已统一修在 `services/materializer.py`、`services/generations.py`、`services/wireguard_keys.py` 三处。

> 通用规律：在 PostgreSQL 上对带 `lazy="joined"` 可空关系的实体做 `with_for_update` 时，必须用 `of=` 限定到非空主表，否则报上述错误。新增此类锁查询时照此处理。
