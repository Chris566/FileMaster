"""撤销栈测试."""

from __future__ import annotations

import json
from pathlib import Path

from filemaster.core.undo import UndoEntry, UndoStack


class TestUndoStack:
    """栈行为."""

    def test_empty(self) -> None:
        s = UndoStack()
        assert len(s) == 0
        assert s.pop() is None

    def test_push_pop(self) -> None:
        s = UndoStack()
        s.push([UndoEntry(operation="CopyOnly", source=Path("a"), target=Path("b"))])
        assert len(s) == 1
        batch = s.pop()
        assert batch is not None
        assert batch[0].operation == "CopyOnly"
        assert len(s) == 0

    def test_empty_batch_not_pushed(self) -> None:
        s = UndoStack()
        s.push([])
        assert len(s) == 0

    def test_lifo_order(self) -> None:
        s = UndoStack()
        s.push([UndoEntry(operation="CopyOnly", target=Path("a"))])
        s.push([UndoEntry(operation="RenameOnly", target=Path("b"))])
        first = s.pop()
        second = s.pop()
        assert first[0].operation == "RenameOnly"
        assert second[0].operation == "CopyOnly"

    def test_max_entries(self) -> None:
        s = UndoStack(max_entries=3)
        for i in range(5):
            s.push([UndoEntry(operation="CopyOnly", target=Path(f"f{i}"))])
        assert len(s) == 3


class TestUndoEntry:
    """序列化."""

    def test_to_from_dict(self) -> None:
        entry = UndoEntry(
            operation="RenameAndCopy",
            source=Path("/a/b.txt"),
            target=Path("/c/d.txt"),
            backup_path=Path("/backup/x.bak"),
        )
        d = entry.to_dict()
        restored = UndoEntry.from_dict(d)
        assert restored.operation == entry.operation
        assert restored.source == entry.source
        assert restored.target == entry.target
        assert restored.backup_path == entry.backup_path

    def test_to_dict_minimal(self) -> None:
        entry = UndoEntry(operation="CopyOnly", source=Path("a"), target=Path("b"))
        d = entry.to_dict()
        assert d["backup_path"] is None

    def test_unique_ids(self) -> None:
        e1 = UndoEntry(operation="CopyOnly", source=Path("a"), target=Path("b"))
        e2 = UndoEntry(operation="CopyOnly", source=Path("a"), target=Path("b"))
        assert e1.entry_id != e2.entry_id


class TestBackup:
    """backup() 静态方法."""

    def test_backup_creates_file(self, tmp_path: Path) -> None:
        target = tmp_path / "src.txt"
        target.write_bytes(b"original content")
        backup_dir = tmp_path / "backups"
        backup = UndoStack.backup(target, backup_dir)
        assert backup.exists()
        assert backup.read_bytes() == b"original content"
        assert backup_dir.exists()
