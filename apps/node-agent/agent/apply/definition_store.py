from __future__ import annotations

"""已应用容器定义的本地持久化。

每次成功 apply 后，把期望容器的定义记录写到
``<state_dir>/nodes/<node_id>/containers/<container>.json``：

```json
{"config_hash": "…", "payload": {…}}
```

用途只有一个：下次 reconcile 判定 recreate 时，若记录哈希与容器 label
吻合，planner 能产出**字段级 diff** 的 reason（"definition changed:
ports, command"），而不是两个看不懂的哈希。记录丢失/损坏不影响判定
正确性——身份的事实来源始终是容器 label，reason 只是降级。

读写都是 best-effort：任何 IO 失败只记日志，不阻断 reconcile。
"""

import json
from pathlib import Path
from typing import Any

from dn42_common import atomic_write_json

from ..core.logging import get_logger
from ..planner.container_plan import ContainerPlan

_LOGGER = get_logger("definition_store")


def load_container_definitions(directory: Path) -> dict[str, dict[str, Any]]:
    """读取全部定义记录，按容器名索引；目录不存在返回空。"""

    records: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return records
    for file in sorted(directory.glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _LOGGER.warning("definition store: 读取 %s 失败：%s", file.name, exc)
            continue
        if isinstance(data, dict) and isinstance(data.get("payload"), dict):
            records[file.stem] = data
    return records


def persist_container_definitions(directory: Path, plan: ContainerPlan) -> None:
    """按计划落盘期望容器的定义记录，并删除不再期望的旧记录。

    幂等自愈：每次成功 apply 后全量重写（KEEP 也写），缺失/过期记录
    自动恢复一致。
    """

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _LOGGER.warning("definition store: 创建目录失败：%s", exc)
        return

    desired = {
        step.container_name: step.definition
        for step in plan.steps
        if step.definition is not None
    }
    for file in directory.glob("*.json"):
        if file.stem not in desired:
            try:
                file.unlink()
            except OSError as exc:
                _LOGGER.warning("definition store: 删除 %s 失败：%s", file.name, exc)
    for name, definition in desired.items():
        record = {"config_hash": definition.config_hash, "payload": definition.payload}
        try:
            atomic_write_json(directory / f"{name}.json", record)
        except OSError as exc:
            _LOGGER.warning("definition store: 写入 %s 失败：%s", name, exc)


__all__ = ["load_container_definitions", "persist_container_definitions"]
