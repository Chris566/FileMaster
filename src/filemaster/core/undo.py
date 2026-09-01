"""多步撤销栈.

W5 详细实现：
- 50 步环形缓冲区
- 每步可独立回滚
- 配置快照联动
- JSON 持久化（%APPDATA%\\FileMaster\\undo\\）
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

OperationType = Literal[
    "CopyOnly",
    "OverwriteOnly",
    "RenameOnly",
    "RenameAndCopy",
    "RenameAndOverwrite",
    "Classify",
    "Delete",
    "Archive",  # W10: 创建归档 (撤销 = 删除 archive_path)
]


@dataclass
class UndoEntry:
    """单条撤销记录."""

    operation: OperationType
    source: Path | None = None
    target: Path | None = None
    backup_path: Path | None = None  # 被覆盖文件备份
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "source": str(self.source) if self.source else None,
            "target": str(self.target) if self.target else None,
            "backup_path": str(self.backup_path) if self.backup_path else None,
            "timestamp": self.timestamp,
            "entry_id": self.entry_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> UndoEntry:
        return cls(
            operation=d["operation"],
            source=Path(d["source"]) if d.get("source") else None,
            target=Path(d["target"]) if d.get("target") else None,
            backup_path=Path(d["backup_path"]) if d.get("backup_path") else None,
            timestamp=d.get("timestamp", ""),
            entry_id=d.get("entry_id", ""),
        )


class UndoStack:
    """撤销栈（环形缓冲，默认保留 50 步）."""

    MAX_ENTRIES = 50

    def __init__(self, persist_dir: Path | None = None, max_entries: int = 50) -> None:
        self._persist_dir = persist_dir
        self._max_entries = max_entries
        self._entries: deque[list[UndoEntry]] = deque(maxlen=max_entries)

    def push(self, batch: list[UndoEntry]) -> None:
        """推入一批新操作（一个 step 由多个 entry 组成）."""
        if not batch:
            return
        self._entries.append(batch)
        self._persist()

    def pop(self) -> list[UndoEntry] | None:
        """弹出一批（最新 step）."""
        if not self._entries:
            return None
        batch = self._entries.pop()
        self._persist()
        return batch

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    @staticmethod
    def backup(target: Path, backup_dir: Path) -> Path:
        """备份将被覆盖的文件.

        Args:
            target: 即将被覆盖的文件
            backup_dir: 备份目录
        Returns:
            备份文件路径
        """
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{uuid.uuid4().hex}.bak"
        shutil.copy2(target, backup_path)
        return backup_path

    def _persist(self) -> None:
        """持久化到 JSON（W5 详细实现）."""
        if self._persist_dir is None:
            return
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "entries": [[e.to_dict() for e in batch] for batch in self._entries],
        }
        path = self._persist_dir / "stack.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        path = self._persist_dir / "stack.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# W10 follow-up: 撤销 dispatcher + UndoStack.restore_latest
# ============================================================


@dataclass
class RestoreEntryResult:
    """单条 UndoEntry 还原结果."""

    target: Path | None
    operation: str
    success: bool
    skipped: bool = False
    error: str | None = None
    message: str = ""


def restore_entry(
    entry: UndoEntry,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> RestoreEntryResult:
    """单条 UndoEntry 还原 dispatcher.

    按 operation 类型分发:
      - Archive / CopyOnly : 删除 target (副本/归档, 源未动)
      - Classify           : 移动 target 回 source (反向 classify move)
      - Delete             : 拒绝 (不可恢复)
      - RenameAndOverwrite / RenameAndCopy / RenameOnly / OverwriteOnly:
        从 backup_path 还原到 target
      - 其他               : 拒绝 (未实现)
    """
    op = entry.operation
    target = entry.target

    # Delete 不可恢复
    if op == "Delete":
        return RestoreEntryResult(
            target=target, operation=op, success=False,
            error="Delete 操作不可恢复 (源文件已永久删除)",
        )

    # Archive / CopyOnly: 删 target
    if op in ("Archive", "CopyOnly"):
        if target is None:
            return RestoreEntryResult(
                target=None, operation=op, success=False,
                error="target 为空, 无法还原",
            )
        if not target.exists():
            return RestoreEntryResult(
                target=target, operation=op, success=True, skipped=True,
                message="归档文件已不存在, 跳过",
            )
        if dry_run:
            return RestoreEntryResult(
                target=target, operation=op, success=True,
                message="[DRY-RUN] 将删除",
            )
        try:
            target.unlink()
        except OSError as e:
            return RestoreEntryResult(
                target=target, operation=op, success=False,
                error=f"删除失败: {e}",
            )
        return RestoreEntryResult(
            target=target, operation=op, success=True, message="已删除",
        )

    # Classify: 移动 target 回 source
    if op == "Classify":
        if target is None or entry.source is None:
            return RestoreEntryResult(
                target=target, operation=op, success=False,
                error="source/target 为空, 无法还原",
            )
        if not target.exists():
            return RestoreEntryResult(
                target=target, operation=op, success=True, skipped=True,
                message="已移动走的文件不存在, 跳过",
            )
        if entry.source.exists() and not overwrite:
            return RestoreEntryResult(
                target=target, operation=op, success=True, skipped=True,
                error="源位置已有文件 (传 overwrite=True 强制覆盖)",
            )
        if dry_run:
            return RestoreEntryResult(
                target=target, operation=op, success=True,
                message="[DRY-RUN] 将移动回去",
            )
        try:
            entry.source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(entry.source))
        except OSError as e:
            return RestoreEntryResult(
                target=target, operation=op, success=False,
                error=f"移动失败: {e}",
            )
        return RestoreEntryResult(
            target=target, operation=op, success=True,
            message=f"已移回 {entry.source}",
        )

    # Rename/Overwrite 系: 从 backup_path 还原到 target
    if op in ("RenameAndOverwrite", "RenameAndCopy", "RenameOnly", "OverwriteOnly"):
        if entry.backup_path is None:
            return RestoreEntryResult(
                target=target, operation=op, success=False,
                error="无 backup_path, 无法还原",
            )
        if not entry.backup_path.exists():
            return RestoreEntryResult(
                target=target, operation=op, success=False,
                error=f"备份文件已丢失: {entry.backup_path}",
            )
        if target is not None and target.exists() and not overwrite:
            return RestoreEntryResult(
                target=target, operation=op, success=True, skipped=True,
                error="target 已存在 (传 overwrite=True 强制覆盖)",
            )
        if dry_run:
            return RestoreEntryResult(
                target=target, operation=op, success=True,
                message="[DRY-RUN] 将从备份还原",
            )
        try:
            if target is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry.backup_path), str(target))
        except OSError as e:
            return RestoreEntryResult(
                target=target, operation=op, success=False,
                error=f"还原失败: {e}",
            )
        return RestoreEntryResult(
            target=target, operation=op, success=True,
            message=f"已从备份还原 {entry.backup_path}",
        )

    # 未实现
    return RestoreEntryResult(
        target=target, operation=op, success=False,
        error=f"operation={op} 尚未实现 dispatcher",
    )


class _UndoStackDispatcherMixin:
    """UndoStack 的 dispatcher 方法 (通过 monkey-patch / 直接挂入).

    W10 follow-up: 让 UndoStack 也能一键弹 + 还原最近 step.
    实际 UndoStack 类定义在前面, 本 mixin 仅为说明.
    """

    def restore_latest(
        self,
        *,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> list[RestoreEntryResult]:
        """弹出最近一批 step 并 dispatcher 还原.

        Returns:
            每条 entry 的 RestoreEntryResult; 栈空时返 [].
        """
        batch = self.pop()  # type: ignore[attr-defined]
        if batch is None:
            return []
        return [restore_entry(e, overwrite=overwrite, dry_run=dry_run) for e in batch]


# 把 restore_latest 注入 UndoStack (避免大改动)
UndoStack.restore_latest = _UndoStackDispatcherMixin.restore_latest  # type: ignore[attr-defined]
