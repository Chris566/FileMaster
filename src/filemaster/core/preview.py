"""实时预览生成.

W6 详细实现：
- 取前 N 个文件
- 应用模板生成预览
- 增量刷新（文件变更时）
- DataGridView 适配
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from filemaster.core.renamer import Renamer


@dataclass(frozen=True)
class PreviewItem:
    """单个文件预览条目."""

    source: Path
    new_name: str
    will_rename: bool
    status: str  # "PREVIEW" | "SKIPPED" | "CONFLICT"


class PreviewGenerator:
    """预览生成器."""

    def __init__(self, renamer: Renamer, limit: int = 100) -> None:
        self._renamer = renamer
        self._limit = limit

    def generate(self, files: Iterable[Path]) -> list[PreviewItem]:
        """生成预览.

        Args:
            files: 文件列表
        Returns:
            前 N 个文件的预览
        """
        items: list[PreviewItem] = []
        for i, file in enumerate(files):
            if i >= self._limit:
                break
            results = self._renamer.plan([file])
            if not results:
                continue
            r = results[0]
            will_rename = r.status not in ("SKIPPED",)
            new_name = r.target.name if r.target else file.name
            items.append(
                PreviewItem(
                    source=file,
                    new_name=new_name,
                    will_rename=will_rename,
                    status=r.status,
                )
            )
            # 重置序号（plan 会自增）
            self._renamer.reset_index()
        return items
