"""重命名引擎.

W2 详细实现：
- 文件扫描（递归/非递归）
- 模板应用
- 冲突检测
- 长路径支持（\\\\?\\ 前缀）
- 异步批处理
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from filemaster.core.template import Template


@dataclass(frozen=True)
class RenameResult:
    """单个文件的重命名结果."""

    source: Path
    target: Path | None
    status: str  # "OK" | "SKIPPED" | "CONFLICT" | "ERROR" | "DRY_RUN"
    message: str = ""


class Renamer:
    """重命名引擎（不执行 IO，只生成 RenameResult）.

    W2 详细实现：FileMover / 真实文件操作 / 撤销回写。
    """

    def __init__(self, template: Template, prefix: str = "", start_index: int = 1) -> None:
        self._template = template
        self._prefix = prefix
        self._start_index = start_index
        self._index = start_index

    def reset_index(self) -> None:
        """重置序号到起始值."""
        self._index = self._start_index

    def _context_for(self, file: Path) -> dict[str, object]:
        """构造单个文件的占位符上下文."""
        return {
            "Prefix": self._prefix,
            "OriginalName": file.name,
            "BaseName": file.stem,
            "Extension": file.suffix.lstrip("."),
            "Index": self._index,
        }

    def plan(self, files: Iterable[Path]) -> list[RenameResult]:
        """规划：只生成结果，不实际改文件.

        Args:
            files: 源文件列表
        Returns:
            RenameResult 列表
        """
        results: list[RenameResult] = []
        for file in files:
            ctx = self._context_for(file)
            new_name = self._template.render(ctx)
            if not new_name or new_name == file.name:
                results.append(RenameResult(file, None, "SKIPPED", "模板未变"))
            else:
                target = file.with_name(new_name)
                results.append(RenameResult(file, target, "DRY_RUN"))
            self._index += 1
        return results

    def already_has_prefix(self, file: Path) -> bool:
        """判断文件是否已带前缀（不重复加）."""
        if not self._prefix:
            return False
        return file.stem.lower().startswith(self._prefix.lower())

    @staticmethod
    def sanitize(name: str) -> str:
        """去除 Windows 非法字符.

        Args:
            name: 原始文件名
        Returns:
            清理后的文件名
        """
        # Windows 非法字符：<>:"/\\|?*，以及控制字符
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
