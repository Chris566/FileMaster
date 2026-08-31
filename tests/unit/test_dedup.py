"""W4 v4 Dedup 模块单元测试.

W4 v3 覆盖(原 39 个):
- DuplicateFile / DuplicateGroup / DedupStats dataclass
- Deduper.find_duplicates / get_stats / find_duplicates_with_meta
- _stat_safe 跨平台兜底
- find_duplicates_in_dir 同步入口

W4 v4 新增(本版):
- ActionResult / BatchActionResult dataclass
- move_duplicates 同步函数(成功/失败/dry-run/跨设备/目标已存在)
- delete_duplicates 同步函数(成功/dry-run/use_trash 错)
- hardlink_duplicates 同步函数(成功/dry-run/Windows 报错文案)
- _write_undo_log 落盘/路径/dry_run 不写
- _safe_send2trash 缺包报错
- 跨平台兼容 (Windows / Unix)
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import time
from pathlib import Path

import pytest

from filemaster.core.dedup import (
    ActionResult,
    BatchActionResult,
    Deduper,
    DedupStats,
    DuplicateFile,
    DuplicateGroup,
    delete_duplicates,
    hardlink_duplicates,
    move_duplicates,
)
from filemaster.workers.dedup import find_duplicates_in_dir


@pytest.fixture
def dup_tree(tmp_path: Path) -> Path:
    """建一个带 2 组重复 + 1 个独立的目录树.

    结构:
        a.txt + a_copy.txt (相同内容 "hello")
        b.txt + b_copy1.txt + b_copy2.txt (相同内容 "world")
        c.txt (独立)
    """
    a = tmp_path / "a.txt"
    a.write_text("hello", encoding="utf-8")
    a_copy = tmp_path / "a_copy.txt"
    a_copy.write_text("hello", encoding="utf-8")

    b = tmp_path / "b.txt"
    b.write_text("world", encoding="utf-8")
    b_copy1 = tmp_path / "b_copy1.txt"
    b_copy1.write_text("world", encoding="utf-8")
    b_copy2 = tmp_path / "b_copy2.txt"
    b_copy2.write_text("world", encoding="utf-8")

    c = tmp_path / "c.txt"
    c.write_text("unique", encoding="utf-8")

    # 给 b 系列设置最早的 mtime
    old = time.time() - 1000
    os.utime(b, (old, old))

    return tmp_path


def _make_group(paths: list[Path]) -> DuplicateGroup:
    """从 paths 列表建一个 DuplicateGroup, 包含 metadata."""
    files_with_meta = []
    for p in paths:
        st = p.stat()
        files_with_meta.append(DuplicateFile(
            path=p, size=st.st_size, mtime=st.st_mtime, ctime=st.st_ctime,
        ))
    size = files_with_meta[0].size
    return DuplicateGroup(
        hash_value="abc123",
        algorithm="md5",
        files=tuple(paths),
        files_with_meta=tuple(files_with_meta),
        hash_size=size,
        wasted_bytes=size * (len(paths) - 1),
    )


# ----- W4 v4：ActionResult / BatchActionResult -----

class TestActionResult:
    def test_construction_success(self, tmp_path: Path) -> None:
        r = ActionResult(
            source=tmp_path / "a.txt",
            target=tmp_path / "_duplicates" / "a.txt",
            action="move",
            success=True,
        )
        assert r.success is True
        assert r.error is None
        assert r.dry_run is False

    def test_construction_failure(self, tmp_path: Path) -> None:
        r = ActionResult(
            source=tmp_path / "a.txt",
            target=None,
            action="delete",
            success=False,
            error="permission denied",
        )
        assert r.success is False
        assert r.error == "permission denied"

    def test_to_dict(self, tmp_path: Path) -> None:
        r = ActionResult(
            source=tmp_path / "a.txt",
            target=tmp_path / "b.txt",
            action="hardlink",
            success=True,
        )
        d = r.to_dict()
        assert d["action"] == "hardlink"
        assert d["success"] is True
        assert d["dry_run"] is False


class TestBatchActionResult:
    def test_empty_results(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        batch = BatchActionResult(group=group, action="move", dry_run=False)
        assert batch.success_count == 0
        assert batch.fail_count == 0
        assert batch.saved_bytes == 0

    def test_success_count(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        results = [
            ActionResult(source=dup_tree / "a.txt", target=None, action="move", success=True),
            ActionResult(source=dup_tree / "a_copy.txt", target=None, action="move", success=False, error="x"),
        ]
        batch = BatchActionResult(group=group, action="move", dry_run=False, results=results)
        assert batch.success_count == 1
        assert batch.fail_count == 1
        assert batch.saved_bytes == group.hash_size  # 1 个成功 × size


# ----- W4 v4：move_duplicates -----

class TestMoveDuplicates:
    def test_dry_run_no_filesystem_change(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        target = dup_tree / "_duplicates"
        batch = move_duplicates(group, target, dry_run=True)
        # 所有结果 dry_run=True
        assert all(r.dry_run for r in batch.results)
        assert all(r.success for r in batch.results)
        # 文件没动
        assert (dup_tree / "a.txt").exists()
        assert (dup_tree / "a_copy.txt").exists()
        assert not target.exists()
        # undo 不写
        assert batch.undo_log_path is None

    def test_real_move_creates_target(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        target = dup_tree / "_duplicates"
        batch = move_duplicates(group, target, dry_run=False)
        assert batch.success_count == 1  # 只有 a_copy.txt 被移
        assert batch.fail_count == 0
        # a.txt 还在原位 (keeper)
        assert (dup_tree / "a.txt").exists()
        # a_copy.txt 移到 _duplicates/
        assert (target / "a_copy.txt").exists()
        # undo 日志写了
        assert batch.undo_log_path is not None
        assert batch.undo_log_path.exists()

    def test_default_target_dir(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        batch = move_duplicates(group, dry_run=False)
        # 默认 target = keeper.parent / "_duplicates"
        expected = dup_tree / "_duplicates" / "a_copy.txt"
        assert batch.results[0].target == expected
        assert expected.exists()

    def test_target_exists_skip(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        target = dup_tree / "_duplicates"
        target.mkdir()
        # 预先放一个同名文件
        (target / "a_copy.txt").write_text("different", encoding="utf-8")
        batch = move_duplicates(group, target, dry_run=False, overwrite=False)
        # 应该被跳过（target 已存在）
        assert batch.fail_count == 1
        assert "已存在" in batch.results[0].error
        # 原文件没动
        assert (dup_tree / "a_copy.txt").exists()

    def test_target_exists_overwrite(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        target = dup_tree / "_duplicates"
        target.mkdir()
        (target / "a_copy.txt").write_text("different", encoding="utf-8")
        batch = move_duplicates(group, target, dry_run=False, overwrite=True)
        assert batch.success_count == 1
        # 目标文件被覆盖
        assert (target / "a_copy.txt").read_text(encoding="utf-8") == "hello"

    def test_undo_log_content(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        batch = move_duplicates(group, dry_run=False)
        assert batch.undo_log_path is not None
        data = json.loads(batch.undo_log_path.read_text(encoding="utf-8"))
        assert data["action"] == "move"
        assert data["group_hash"] == group.hash_value
        assert data["keeper"] == str(group.keeper)
        assert len(data["entries"]) == 1
        assert data["entries"][0]["op"] == "move"
        assert data["entries"][0]["to"] == str(dup_tree / "a_copy.txt")

    def test_cannot_create_target_dir(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        # 目标 = 现有文件, 不能当目录
        bad_target = dup_tree / "not_a_dir.txt"
        bad_target.write_text("blocking", encoding="utf-8")
        batch = move_duplicates(group, bad_target, dry_run=False)
        assert batch.success_count == 0
        assert batch.fail_count == 1
        assert "无法创建目标目录" in batch.results[0].error


# ----- W4 v4：delete_duplicates -----

class TestDeleteDuplicates:
    def test_dry_run_no_filesystem_change(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        batch = delete_duplicates(group, dry_run=True)
        assert all(r.dry_run for r in batch.results)
        # 文件没动
        assert (dup_tree / "a.txt").exists()
        assert (dup_tree / "a_copy.txt").exists()

    def test_real_delete_use_trash_missing_package(self, dup_tree: Path, monkeypatch) -> None:
        """没装 send2trash 时 use_trash=True 应该 raise ImportError."""
        # 模拟 send2trash 未装: 把 sys.modules['send2trash'] 设为 None 让 import 失败
        import sys
        monkeypatch.setitem(sys.modules, "send2trash", None)
        # 同时也清掉已经 import 过的缓存
        from filemaster.core import dedup as _dedup_mod
        monkeypatch.setattr(_dedup_mod, "send2trash", None, raising=False)
        from filemaster.core.dedup import _safe_send2trash
        with pytest.raises(ImportError, match="send2trash"):
            _safe_send2trash(dup_tree / "a_copy.txt")

    def test_real_delete_without_trash(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        batch = delete_duplicates(group, dry_run=False, use_trash=False)
        assert batch.success_count == 1
        # keeper 还在
        assert (dup_tree / "a.txt").exists()
        # duplicate 删了
        assert not (dup_tree / "a_copy.txt").exists()
        # undo 日志写了
        assert batch.undo_log_path is not None
        data = json.loads(batch.undo_log_path.read_text(encoding="utf-8"))
        assert data["action"] == "delete"
        assert data["entries"][0]["op"] == "delete"

    def test_delete_nonexistent_file(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        # 先删一个, 再让 delete_duplicates 处理（应失败但不影响 keeper）
        (dup_tree / "a_copy.txt").unlink()
        batch = delete_duplicates(group, dry_run=False, use_trash=False)
        assert batch.success_count == 0
        assert batch.fail_count == 1
        # a.txt (keeper) 还在
        assert (dup_tree / "a.txt").exists()

    def test_delete_three_copies(self, dup_tree: Path) -> None:
        paths = [dup_tree / "b.txt", dup_tree / "b_copy1.txt", dup_tree / "b_copy2.txt"]
        group = _make_group(paths)
        batch = delete_duplicates(group, dry_run=False, use_trash=False)
        # 3 文件 - 1 keeper = 2 个 delete
        assert batch.success_count == 2
        assert batch.fail_count == 0
        # keeper 还在
        assert (dup_tree / "b.txt").exists()
        assert not (dup_tree / "b_copy1.txt").exists()
        assert not (dup_tree / "b_copy2.txt").exists()


# ----- W4 v4：hardlink_duplicates -----

class TestHardlinkDuplicates:
    def test_dry_run_no_filesystem_change(self, dup_tree: Path) -> None:
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        batch = hardlink_duplicates(group, dry_run=True)
        assert all(r.dry_run for r in batch.results)
        # 文件没动
        assert (dup_tree / "a.txt").exists()
        assert (dup_tree / "a_copy.txt").exists()

    def test_real_hardlink_unix(self, dup_tree: Path) -> None:
        """Unix 类的硬链测试 (Windows 上可能因权限跳过)."""
        if platform.system() == "Windows":
            pytest.skip("Windows 硬链需要特殊权限, 跳过")
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        keeper_inode = (dup_tree / "a.txt").stat().st_ino
        batch = hardlink_duplicates(group, dry_run=False)
        assert batch.success_count == 1
        # 硬链后两文件 inode 相同
        assert (dup_tree / "a_copy.txt").stat().st_ino == keeper_inode
        # 改一个, 另一个跟着变 (硬链特征)
        (dup_tree / "a.txt").write_text("modified", encoding="utf-8")
        assert (dup_tree / "a_copy.txt").read_text(encoding="utf-8") == "modified"
        # hardlink 不写 undo log
        assert batch.undo_log_path is None

    def test_hardlink_windows_hint(self, dup_tree: Path) -> None:
        """Windows 上失败时错误信息要带 hint."""
        if platform.system() != "Windows":
            pytest.skip("非 Windows 平台不验 Windows 提示")
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        batch = hardlink_duplicates(group, dry_run=False)
        # Windows 必失败, 错误里要有 hint
        if batch.fail_count > 0:
            assert "Windows" in batch.results[0].error or "硬链" in batch.results[0].error

    def test_hardlink_overwrites_by_design(self, dup_tree: Path) -> None:
        """硬链操作本质就是「删旧 + 建链」, 不需要 overwrite 参数.

        跟 move 不同: 硬链的 src 和 target 是同一路径, 所以 overwrite 检查无意义.
        """
        if platform.system() == "Windows":
            pytest.skip("Windows 硬链需要特殊权限, 跳过")
        # 把 a_copy 内容改成不同的
        (dup_tree / "a_copy.txt").write_text("totally different", encoding="utf-8")
        group = _make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"])
        batch = hardlink_duplicates(group, dry_run=False)
        # 硬链应该成功, 旧 a_copy 内容被覆盖
        assert batch.success_count == 1
        # a_copy 现在是 a.txt 的硬链, 改 a.txt 同步到 a_copy
        (dup_tree / "a.txt").write_text("changed again", encoding="utf-8")
        assert (dup_tree / "a_copy.txt").read_text(encoding="utf-8") == "changed again"

    def test_hardlink_three_copies_unix(self, dup_tree: Path) -> None:
        if platform.system() == "Windows":
            pytest.skip("Windows 硬链需要特殊权限")
        paths = [dup_tree / "b.txt", dup_tree / "b_copy1.txt", dup_tree / "b_copy2.txt"]
        group = _make_group(paths)
        batch = hardlink_duplicates(group, dry_run=False)
        assert batch.success_count == 2  # 3 - 1 keeper
        keeper_inode = (dup_tree / "b.txt").stat().st_ino
        assert (dup_tree / "b_copy1.txt").stat().st_ino == keeper_inode
        assert (dup_tree / "b_copy2.txt").stat().st_ino == keeper_inode


# ----- W4 v4：_write_undo_log -----

class TestUndoLog:
    def test_dry_run_does_not_write(self, dup_tree: Path) -> None:
        from filemaster.core.dedup import _write_undo_log
        path = _write_undo_log(
            group=_make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"]),
            action="move",
            entries=[{"op": "move", "from": "x", "to": "y"}],
            dry_run=True,
        )
        assert path is None

    def test_hardlink_does_not_write(self, dup_tree: Path) -> None:
        from filemaster.core.dedup import _write_undo_log
        path = _write_undo_log(
            group=_make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"]),
            action="hardlink",
            entries=[{"op": "hardlink", "path": "x", "keeper": "y"}],
            dry_run=False,
        )
        assert path is None

    def test_empty_entries_does_not_write(self, dup_tree: Path) -> None:
        from filemaster.core.dedup import _write_undo_log
        path = _write_undo_log(
            group=_make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"]),
            action="move",
            entries=[],
            dry_run=False,
        )
        assert path is None

    def test_real_write_creates_file(self, dup_tree: Path) -> None:
        from filemaster.core.dedup import _write_undo_log
        path = _write_undo_log(
            group=_make_group([dup_tree / "a.txt", dup_tree / "a_copy.txt"]),
            action="move",
            entries=[{"op": "move", "from": str(dup_tree / "_d" / "a.txt"), "to": str(dup_tree / "a.txt")}],
            dry_run=False,
        )
        assert path is not None
        assert path.exists()
        # 文件在 ~/.filemaster/undo/ 下
        assert ".filemaster" in str(path)
        assert "undo" in str(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["action"] == "move"


# ----- W4 v4：integration with Deduper -----

class TestIntegration:
    """W4 v4：完整闭环 — Deduper.find_duplicates_with_meta + 3 动作函数."""

    def test_full_workflow_dry_run(self, dup_tree: Path) -> None:
        """找重复 + dry-run 移动 → 文件没动."""
        deduper = Deduper(algorithm="md5")
        files = [p for p in dup_tree.iterdir() if p.is_file()]
        groups, stats = deduper.find_duplicates_with_meta(files)
        assert stats.duplicate_groups == 2  # a组 + b组
        for g in groups:
            batch = move_duplicates(g, dry_run=True)
            assert all(r.dry_run for r in batch.results)
            # 所有原文件都还在
            for f in g.files:
                assert f.exists()

    def test_full_workflow_real_delete(self, dup_tree: Path) -> None:
        """找重复 + 真删（不用 trash） → 重复文件没了, keeper 还在."""
        deduper = Deduper(algorithm="md5")
        files = [p for p in dup_tree.iterdir() if p.is_file()]
        groups, stats = deduper.find_duplicates_with_meta(files)
        assert stats.duplicate_groups == 2
        for g in groups:
            keeper = g.keeper
            delete_duplicates(g, dry_run=False, use_trash=False)
            assert keeper.exists()
            for dup in g.duplicates:
                assert not dup.exists()


# ============================================================
# W4 v5: undo log 恢复
# ============================================================


class TestListUndoLogs:
    """list_undo_logs() - 列目录里的 undo JSON."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        from filemaster.core.dedup import list_undo_logs
        assert list_undo_logs(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        from filemaster.core.dedup import list_undo_logs
        # 不存在不抛, 返空
        assert list_undo_logs(tmp_path / "no_such_dir") == []

    def test_lists_valid_logs(self, tmp_path: Path) -> None:
        from filemaster.core.dedup import list_undo_logs
        # 写 3 个 JSON
        for i in range(3):
            (tmp_path / f"2026083{i}_deadbeef_move.json").write_text(
                json.dumps({
                    "action": "move",
                    "timestamp": f"2026083{i}",
                    "group_hash": f"h{i}",
                    "keeper": f"/k/{i}",
                    "entries": [],
                }),
                encoding="utf-8",
            )
        # 写一个损坏 JSON
        (tmp_path / "bad.json").write_text("{ not valid", encoding="utf-8")
        logs = list_undo_logs(tmp_path)
        assert len(logs) == 3  # 损坏的跳过
        # 都是 move action
        assert all(log.action == "move" for log in logs)

    def test_skips_corrupted_json(self, tmp_path: Path) -> None:
        from filemaster.core.dedup import list_undo_logs
        (tmp_path / "bad.json").write_text("not json at all", encoding="utf-8")
        assert list_undo_logs(tmp_path) == []


class TestUndoLogDataclass:
    """W4 v5: UndoLog dataclass + can_restore property."""

    def test_can_restore_move(self, tmp_path: Path) -> None:
        from filemaster.core.dedup import UndoLog
        log = UndoLog(
            path=tmp_path / "x.json",
            action="move",
            timestamp="20260831",
            group_hash="abc",
            keeper="/k",
            entries=[{"op": "move", "from": "/a", "to": "/b"}],
        )
        assert log.can_restore is True
        assert log.entry_count == 1

    def test_cannot_restore_delete(self, tmp_path: Path) -> None:
        from filemaster.core.dedup import UndoLog
        log = UndoLog(
            path=tmp_path / "x.json",
            action="delete",
            timestamp="20260831",
            group_hash="abc",
            keeper="/k",
            entries=[{"op": "delete", "path": "/a"}],
        )
        assert log.can_restore is False

    def test_from_path_roundtrip(self, tmp_path: Path) -> None:
        from filemaster.core.dedup import UndoLog
        p = tmp_path / "test.json"
        p.write_text(json.dumps({
            "action": "move",
            "timestamp": "20260831_120000",
            "group_hash": "abc123",
            "keeper": "/path/to/keeper.txt",
            "entries": [
                {"op": "move", "from": "/a/b.txt", "to": "/c/d.txt"},
            ],
        }), encoding="utf-8")
        log = UndoLog.from_path(p)
        assert log.action == "move"
        assert log.timestamp == "20260831_120000"
        assert log.group_hash == "abc123"
        assert log.keeper == "/path/to/keeper.txt"
        assert log.entry_count == 1


class TestRestoreUndoLog:
    """restore_undo_log() - 恢复 move 操作."""

    def test_restore_move_basic(self, tmp_path: Path) -> None:
        """文件在 _duplicates/, 恢复回原位置."""
        from filemaster.core.dedup import restore_undo_log

        # 准备: keeper 在 /keeper/a.txt, 副本被移到 /keeper/_duplicates/a.txt
        keeper_dir = tmp_path / "keeper"
        keeper_dir.mkdir()
        keeper = keeper_dir / "a.txt"
        keeper.write_text("hello")
        dup_dir = keeper_dir / "_duplicates"
        dup_dir.mkdir()
        dup_at_target = dup_dir / "a_copy.txt"
        dup_at_target.write_text("hello")
        # 原位文件 a_copy.txt 已被 move 走, 现在不存在
        original = keeper_dir / "a_copy.txt"
        assert not original.exists()

        # 写 undo log: from=target (现在位置), to=original (原位)
        log_path = tmp_path / "undo.json"
        log_path.write_text(json.dumps({
            "action": "move",
            "timestamp": "20260831_120000",
            "group_hash": "abc",
            "keeper": str(keeper),
            "entries": [
                {"op": "move", "from": str(dup_at_target), "to": str(original)},
            ],
        }), encoding="utf-8")

        results = restore_undo_log(log_path)
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].target == original
        # 文件移回原位
        assert original.exists()
        # dup 位置空了
        assert not dup_at_target.exists()

    def test_restore_dry_run(self, tmp_path: Path) -> None:
        from filemaster.core.dedup import restore_undo_log
        keeper = tmp_path / "k.txt"
        keeper.write_text("k")
        dup = tmp_path / "_dup" / "k.txt"
        dup.parent.mkdir()
        dup.write_text("k")
        original = tmp_path / "original.txt"
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps({
            "action": "move", "timestamp": "t", "group_hash": "h", "keeper": str(keeper),
            "entries": [{"op": "move", "from": str(dup), "to": str(original)}],
        }), encoding="utf-8")

        results = restore_undo_log(log_path, dry_run=True)
        assert len(results) == 1
        assert results[0].success is True
        # 文件没动
        assert dup.exists()
        assert not original.exists()

    def test_restore_skips_existing_target(self, tmp_path: Path) -> None:
        """目标已存在 → 默认跳过, 返 success=False, skipped=True."""
        from filemaster.core.dedup import restore_undo_log
        keeper = tmp_path / "k.txt"
        keeper.write_text("k")
        dup = tmp_path / "dup" / "k.txt"
        dup.parent.mkdir()
        dup.write_text("k")
        original = tmp_path / "original.txt"
        original.write_text("BLOCKER")  # 占用原位
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps({
            "action": "move", "timestamp": "t", "group_hash": "h", "keeper": str(keeper),
            "entries": [{"op": "move", "from": str(dup), "to": str(original)}],
        }), encoding="utf-8")

        results = restore_undo_log(log_path)  # overwrite=False
        assert results[0].success is False
        assert results[0].skipped is True
        # 文件没动
        assert dup.exists()

    def test_restore_overwrite(self, tmp_path: Path) -> None:
        """overwrite=True 时覆盖目标."""
        from filemaster.core.dedup import restore_undo_log
        keeper = tmp_path / "k.txt"
        keeper.write_text("k")
        dup = tmp_path / "dup" / "k.txt"
        dup.parent.mkdir()
        dup.write_text("NEW")
        original = tmp_path / "original.txt"
        original.write_text("OLD")
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps({
            "action": "move", "timestamp": "t", "group_hash": "h", "keeper": str(keeper),
            "entries": [{"op": "move", "from": str(dup), "to": str(original)}],
        }), encoding="utf-8")

        results = restore_undo_log(log_path, overwrite=True)
        assert results[0].success is True
        # original 被覆盖
        assert original.read_text() == "NEW"
        assert not dup.exists()

    def test_restore_source_missing(self, tmp_path: Path) -> None:
        """源 (target) 文件已不存在 → success=False, error 含信息."""
        from filemaster.core.dedup import restore_undo_log
        keeper = tmp_path / "k.txt"
        keeper.write_text("k")
        original = tmp_path / "original.txt"
        # dup 已经不在了
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps({
            "action": "move", "timestamp": "t", "group_hash": "h", "keeper": str(keeper),
            "entries": [{"op": "move", "from": "/nonexistent", "to": str(original)}],
        }), encoding="utf-8")

        results = restore_undo_log(log_path)
        assert results[0].success is False
        assert "不存在" in (results[0].error or "")

    def test_restore_delete_action_raises(self, tmp_path: Path) -> None:
        """delete 操作无法恢复, 抛 ValueError."""
        from filemaster.core.dedup import restore_undo_log
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps({
            "action": "delete", "timestamp": "t", "group_hash": "h", "keeper": "/k",
            "entries": [{"op": "delete", "path": "/a"}],
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="不可恢复"):
            restore_undo_log(log_path)

    def test_restore_nonexistent_log_raises(self, tmp_path: Path) -> None:
        from filemaster.core.dedup import restore_undo_log
        with pytest.raises(FileNotFoundError):
            restore_undo_log(tmp_path / "no_such.json")

    def test_restore_corrupted_log_raises(self, tmp_path: Path) -> None:
        from filemaster.core.dedup import restore_undo_log
        log_path = tmp_path / "bad.json"
        log_path.write_text("not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            restore_undo_log(log_path)

    def test_restore_multiple_entries(self, tmp_path: Path) -> None:
        """多条 entry 一起恢复."""
        from filemaster.core.dedup import restore_undo_log
        keeper = tmp_path / "k.txt"
        keeper.write_text("k")
        dups = tmp_path / "dups"
        dups.mkdir()
        originals = []
        entries = []
        for i in range(3):
            d = dups / f"f{i}.txt"
            d.write_text(str(i))
            o = tmp_path / f"orig_{i}.txt"
            entries.append({"op": "move", "from": str(d), "to": str(o)})
            originals.append(o)
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps({
            "action": "move", "timestamp": "t", "group_hash": "h", "keeper": str(keeper),
            "entries": entries,
        }), encoding="utf-8")

        results = restore_undo_log(log_path)
        assert len(results) == 3
        assert all(r.success for r in results)
        for o in originals:
            assert o.exists()
