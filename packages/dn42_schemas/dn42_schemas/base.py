from __future__ import annotations

"""`StrictModel`——本包所有协议模型的公共基类。

* `extra="forbid"`：拒绝未知字段，避免控制面与 Agent 间出现隐式协议漂移。
* `frozen=True`：模型不可变。`DesiredState.validate_references` 需要在
  验证后注入 looking-glass services，走的是 `object.__setattr__` 这条
  唯一受控豁口，其他处一律当作不可变对象使用。
"""

from pydantic import BaseModel, ConfigDict

from dn42_common import canonical_json_dumps, canonical_sha256_hex


class StrictModel(BaseModel):
    """本包所有 schema 对象的公共基类。`extra="forbid"` + `frozen=True`。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    def canonical_json(self) -> str:
        """返回稳定可复算的 JSON 表示（排序键、紧凑分隔符、`ensure_ascii=False`），
        使控制面与 Agent 在不同进程对同一对象算出的字节序列一致。"""

        return canonical_json_dumps(self.model_dump(mode="json"))

    def canonical_sha256(self) -> str:
        """`canonical_json()` 的 SHA-256 十六进制摘要。"""

        return canonical_sha256_hex(self.model_dump(mode="json"))

