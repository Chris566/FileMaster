"""重命名引擎.

W2 详细实现：
- 规划（plan）：只生成结果，不动文件
- 应用（apply）：真实文件 IO，支持 3 种冲突策略
- 撤销联动：每个成功操作入 UndoStack
- 冲突策略：skip / overwrite / rename_new
"""

from __future__ import annotations

import contextlib
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from filemaster.core.template import Template
from filemaster.core.undo import UndoEntry, UndoStack
from filemaster.utils.hash import file_hash


class ConflictStrategy(str, Enum):
    """冲突策略."""

    SKIP = "skip"           # 目标已存在则跳过该文件
    OVERWRITE = "overwrite"  # 目标已存在则覆盖（先备份到 undo）
    RENAME_NEW = "rename_new"  # 目标已存在则改名 (1) (2)...

    def __str__(self) -> str:
        return self.value


# 文件大小格式化
def format_size(size: int) -> str:
    """把字节数格式化为人类可读字符串.

    Args:
        size: 字节数
    Returns:
        如 "1.5 MB" / "512 B" / "2.3 GB"
    """
    if size < 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {units[-1]}"  # 不可达


def format_date(epoch: float, fmt: str = "%Y-%m-%d") -> str:
    """把 epoch 格式化为日期字符串.

    Args:
        epoch: Unix timestamp
        fmt: strftime 格式（默认 YYYY-MM-DD）
    Returns:
        格式化后的日期字符串
    """
    try:
        return datetime.fromtimestamp(epoch).strftime(fmt)
    except (OSError, ValueError, OverflowError):
        return ""


def get_excel_sheet_name(file: Path) -> str:
    """读取 Excel 文件的第一个 sheet 名（如果可能）.

    Args:
        file: Excel 文件路径
    Returns:
        第一个 sheet 名；非 Excel / 失败返回 ""
    """
    suffix = file.suffix.lower()
    if suffix not in (".xlsx", ".xlsm"):
        return ""
    wb = None
    try:
        from openpyxl import load_workbook  # type: ignore[import-untyped]

        # 注意：openpyxl 的 Workbook 不是 context manager，
        # 老的 with 用法会 AttributeError，需显式 close
        wb = load_workbook(file, read_only=True, data_only=True)
        # 优先 active.title，没有就取 worksheets[0]
        if wb.active and getattr(wb.active, "title", None):
            return wb.active.title
        if wb.worksheets:
            return wb.worksheets[0].title
        return ""
    except Exception:
        return ""
    finally:
        if wb is not None:
            with contextlib.suppress(Exception):
                wb.close()


@dataclass(frozen=True)
class RenameResult:
    """单个文件的重命名结果."""

    source: Path
    target: Path | None
    status: str  # OK | SKIPPED | CONFLICT | OVERWRITTEN | RENAMED | DRY_RUN | ERROR
    message: str = ""


class Renamer:
    """重命名引擎.

    - plan(): 只生成 RenameResult，不动文件（dry-run）
    - apply(): 真实文件 IO，写 UndoStack（如提供）
    """

    def __init__(self, template: Template, prefix: str = "", start_index: int = 1) -> None:
        self._template = template
        self._prefix = prefix
        self._start_index = start_index
        self._index = start_index

    def reset_index(self) -> None:
        """重置序号到起始值."""
        self._index = self._start_index

    @property
    def template(self) -> Template:
        return self._template

    def _placeholders_used(self) -> set[str]:
        """返回模板中出现的占位符名集合（用于按需计算扩展 context）."""
        return {p.name for p in self._template.placeholders()}

    def _context_for(self, file: Path) -> dict[str, object]:
        """构造单个文件的占位符上下文.

        按模板实际使用的占位符按需计算昂贵的扩展字段（Hash / Sheet）。
        """
        ctx: dict[str, object] = {
            "Prefix": self._prefix,
            "OriginalName": file.name,
            "BaseName": file.stem,
            "Extension": file.suffix.lstrip("."),
            "Index": self._index,
        }

        used = self._placeholders_used()
        need_stat = used & {"FileSize", "FileSizeBytes", "CreatedDate", "ModifiedDate"}
        if need_stat:
            try:
                stat = file.stat()
                if "FileSizeBytes" in used:
                    ctx["FileSizeBytes"] = stat.st_size
                if "FileSize" in used:
                    ctx["FileSize"] = format_size(stat.st_size)
                if "CreatedDate" in used:
                    ctx["CreatedDate"] = format_date(stat.st_ctime)
                if "ModifiedDate" in used:
                    ctx["ModifiedDate"] = format_date(stat.st_mtime)
            except OSError:
                ctx.setdefault("FileSizeBytes", 0)
                ctx.setdefault("FileSize", "0 B")
                ctx.setdefault("CreatedDate", "")
                ctx.setdefault("ModifiedDate", "")

        if used & {"HashShort", "Hash"}:
            try:
                digest = file_hash(file, "md5")
                if "HashShort" in used:
                    ctx["HashShort"] = digest[:8]
                if "Hash" in used:
                    ctx["Hash"] = digest
            except OSError:
                ctx.setdefault("HashShort", "")
                ctx.setdefault("Hash", "")

        if "Sheet" in used:
            ctx["Sheet"] = get_excel_sheet_name(file)

        return ctx

    def _render_target(self, file: Path) -> Path | None:
        """根据模板 + context 渲染出新文件名.

        Returns:
            新文件路径；如果渲染结果与原名相同则返回 None
        """
        ctx = self._context_for(file)
        new_name = self._template.render(ctx)
        if not new_name or new_name == file.name:
            return None
        # 用 sanitize 兜底
        new_name = self.sanitize(new_name)
        return file.with_name(new_name)

    def plan(self, files: Iterable[Path]) -> list[RenameResult]:
        """规划：只生成结果，不实际改文件.

        Args:
            files: 源文件列表
        Returns:
            RenameResult 列表（status="DRY_RUN" 或 "SKIPPED"）
        """
        results: list[RenameResult] = []
        for file in files:
            target = self._render_target(file)
            if target is None:
                results.append(RenameResult(file, None, "SKIPPED", "模板未变"))
            else:
                results.append(RenameResult(file, target, "DRY_RUN"))
            self._index += 1
        return results

    def apply(
        self,
        files: Iterable[Path],
        conflict_strategy: ConflictStrategy = ConflictStrategy.SKIP,
        undo_stack: UndoStack | None = None,
    ) -> list[RenameResult]:
        """真实执行重命名.

        Args:
            files: 源文件列表
            conflict_strategy: 冲突策略（skip / overwrite / rename_new）
            undo_stack: 可选，提供则把每次成功操作写入栈
        Returns:
            RenameResult 列表
        """
        results: list[RenameResult] = []
        entries: list[UndoEntry] = []

        for file in files:
            target = self._render_target(file)
            if target is None:
                results.append(RenameResult(file, None, "SKIPPED", "模板未变"))
                self._index += 1
                continue

            # 冲突检测
            if target.exists():
                if conflict_strategy is ConflictStrategy.SKIP:
                    results.append(
                        RenameResult(file, target, "CONFLICT", f"目标已存在：{target.name}（已跳过）")
                    )
                    self._index += 1
                    continue
                if conflict_strategy is ConflictStrategy.RENAME_NEW:
                    target = self._find_free_name(target)
                    if target is None:
                        results.append(
                            RenameResult(file, None, "ERROR", f"无法找到空闲名：{file.name}")
                        )
                        self._index += 1
                        continue
                    # rename_new 的最终结果：用了新名
                    final_status = "RENAMED"
                    final_msg = f"已避开冲突：{target.name}"
                else:  # OVERWRITE
                    final_status = "OVERWRITTEN"
                    final_msg = f"已覆盖：{target.name}"
            else:
                final_status = "OK"
                final_msg = ""

            # 执行 rename
            try:
                backup_path: Path | None = None
                if (
                    conflict_strategy is ConflictStrategy.OVERWRITE
                    and target.exists()
                    and undo_stack is not None
                ):
                    backup_path = UndoStack.backup(target, self._backup_dir(undo_stack))
                file.rename(target)
            except OSError as e:
                results.append(RenameResult(file, target, "ERROR", str(e)))
                self._index += 1
                continue

            results.append(RenameResult(file, target, final_status, final_msg))

            # 写 UndoEntry
            if undo_stack is not None:
                entries.append(
                    UndoEntry(
                        operation="RenameOnly",
                        source=file,
                        target=target,
                        backup_path=backup_path,
                    )
                )

            self._index += 1

        # 一次性入栈
        if undo_stack is not None and entries:
            undo_stack.push(entries)

        return results

    @staticmethod
    def _backup_dir(undo_stack: UndoStack) -> Path:
        """根据 UndoStack 推断备份目录."""
        if undo_stack._persist_dir is not None:
            return undo_stack._persist_dir / "backups"
        # 兜底：用户态临时
        import tempfile

        return Path(tempfile.gettempdir()) / "filemaster_backups"

    @staticmethod
    def _find_free_name(target: Path) -> Path | None:
        """找一个空闲的新名：name (1).ext / name (2).ext / ...

        上限 9999，避免无限循环。
        """
        stem, ext = target.stem, target.suffix
        for i in range(1, 10_000):
            candidate = target.with_name(f"{stem} ({i}){ext}")
            if not candidate.exists():
                return candidate
        return None

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
        # Windows 非法字符：<>:"/\\|?*，以及控制字符；尾部空格/点也清掉
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
        return cleaned.rstrip(" .")
