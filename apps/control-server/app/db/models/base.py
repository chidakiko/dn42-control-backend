"""``app.db.base`` 在 ``models`` 包里的桥接，便于模型文件用相对导入拿到 ``Base``。"""

from ..base import Base

__all__ = ["Base"]
