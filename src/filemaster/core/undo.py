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
