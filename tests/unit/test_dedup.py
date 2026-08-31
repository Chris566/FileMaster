"""W4 v3 Dedup 模块单元测试.

覆盖：
- DuplicateFile / DuplicateGroup / DedupStats dataclass
- Deduper.find_duplicates（W2 兼容 API）
- Deduper.get_stats（W2 兼容 API）
- Deduper.find_duplicates_with_meta（W4 v3 新 API）
- _stat_safe 跨平台兜底
- find_duplicates_in_dir 同步入口

注：DedupWorker 异步逻辑走集成测试 (test_dedup_integration.py)，不在本单测。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from filemaster.core.dedup import (
    Deduper,
    DedupStats,
    DuplicateFile,
    DuplicateGroup,
    _stat_safe,
)
from filemaster.workers.dedup import find_duplicates_in_dir

# ====================================================================
# Fixtures
# ====================================================================


@pytest.fixture
def dup_tree(tmp_path: Path) -> Path:
    """构造有重复的小目录.

    布局：
        a.txt  "hello"  (md5 唯一)
        b.txt  "world"  (md5 唯一)
        c.txt  "same"   ┐ 重复
        d.txt  "same"   ┘
        e.txt  "same"   ┘
        nested/x.txt "same"  ┘  同组
    """
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "b.txt").write_text("world", encoding="utf-8")
    (tmp_path / "c.txt").write_text("same", encoding="utf-8")
    (tmp_path / "d.txt").write_text("same", encoding="utf-8")
    (tmp_path / "e.txt").write_text("same", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "x.txt").write_text("same", encoding="utf-8")
    return tmp_path


# ====================================================================
# DuplicateFile
# ====================================================================


class TestDuplicateFile:
    def test_construction(self, tmp_path: Path) -> None:
        f = DuplicateFile(path=tmp_path / "x.txt", size=10, mtime=1.5, ctime=2.0)
        assert f.path == tmp_path / "x.txt"
        assert f.size == 10
        assert f.mtime == 1.5
        assert f.ctime == 2.0

    def test_frozen(self, tmp_path: Path) -> None:
        f = DuplicateFile(path=tmp_path / "x.txt", size=10, mtime=1.5, ctime=2.0)
        with pytest.raises((AttributeError, Exception)):
            f.size = 20  # type: ignore[misc]

    def test_equality(self, tmp_path: Path) -> None:
        a = DuplicateFile(path=tmp_path / "x.txt", size=10, mtime=1.5, ctime=2.0)
        b = DuplicateFile(path=tmp_path / "x.txt", size=10, mtime=1.5, ctime=2.0)
        c = DuplicateFile(path=tmp_path / "y.txt", size=10, mtime=1.5, ctime=2.0)
        assert a == b
        assert a != c


# ====================================================================
# DuplicateGroup
# ====================================================================


class TestDuplicateGroup:
    def test_construction(self, tmp_path: Path) -> None:
        p1, p2 = tmp_path / "a.txt", tmp_path / "b.txt"
        g = DuplicateGroup(
            hash_value="abc",
            algorithm="md5",
            files=(p1, p2),
        )
        assert g.count == 2
        assert g.hash_size == 0
        assert g.wasted_bytes == 0

    def test_too_few_files_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="必须至少 2 个文件"):
            DuplicateGroup(
                hash_value="abc",
                algorithm="md5",
                files=(tmp_path / "only.txt",),
            )

    def test_keeper_with_meta(self, tmp_path: Path) -> None:
        p1, p2, p3 = tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "c.txt"
        meta = (
            DuplicateFile(p1, 10, mtime=2.0, ctime=0.0),
            DuplicateFile(p2, 10, mtime=1.0, ctime=0.0),  # 最早 → keeper
            DuplicateFile(p3, 10, mtime=3.0, ctime=0.0),
        )
        g = DuplicateGroup(
            hash_value="h",
            algorithm="md5",
            files=(p1, p2, p3),
            files_with_meta=meta,
        )
        assert g.keeper == p2

    def test_keeper_without_meta_falls_back_to_stat(
        self, tmp_path: Path
    ) -> None:
        p1 = tmp_path / "old.txt"
        p2 = tmp_path / "new.txt"
        p1.write_text("same")
        # 强制让 p1 比 p2 旧
        import time
        time.sleep(0.05)
        p2.write_text("same")
        g = DuplicateGroup(
            hash_value="h",
            algorithm="md5",
            files=(p1, p2),
        )
        # p1 是较早的 → keeper
        assert g.keeper == p1

    def test_duplicates_excludes_keeper(self, tmp_path: Path) -> None:
        p1, p2, p3 = tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "c.txt"
        meta = (
            DuplicateFile(p1, 10, mtime=2.0, ctime=0.0),
            DuplicateFile(p2, 10, mtime=1.0, ctime=0.0),  # keeper
            DuplicateFile(p3, 10, mtime=3.0, ctime=0.0),
        )
        g = DuplicateGroup(
            hash_value="h",
            algorithm="md5",
            files=(p1, p2, p3),
            files_with_meta=meta,
        )
        assert p2 not in g.duplicates
        assert set(g.duplicates) == {p1, p3}
        assert len(g.duplicates) == 2

    def test_count(self, tmp_path: Path) -> None:
        p1, p2, p3, p4 = [tmp_path / f"f{i}.txt" for i in range(4)]
        g = DuplicateGroup(hash_value="h", algorithm="md5", files=(p1, p2, p3, p4))
        assert g.count == 4


# ====================================================================
# DedupStats
# ====================================================================


class TestDedupStats:
    def test_default_values(self) -> None:
        s = DedupStats()
        assert s.total_files == 0
        assert s.duplicate_groups == 0
        assert s.duplicate_files == 0
        assert s.wasted_bytes == 0
        assert s.duration_ms == 0

    def test_wasted_human_bytes(self) -> None:
        assert DedupStats(wasted_bytes=512).wasted_human == "512 B"

    def test_wasted_human_kb(self) -> None:
        assert DedupStats(wasted_bytes=2048).wasted_human == "2.0 KB"

    def test_wasted_human_mb(self) -> None:
        assert DedupStats(wasted_bytes=5 * 1024 * 1024).wasted_human == "5.0 MB"

    def test_wasted_human_gb(self) -> None:
        assert DedupStats(wasted_bytes=2 * 1024 ** 3).wasted_human == "2.0 GB"

    def test_wasted_human_tb(self) -> None:
        assert DedupStats(wasted_bytes=3 * 1024 ** 4).wasted_human == "3.0 TB"

    def test_to_dict(self) -> None:
        s = DedupStats(
            total_files=10, duplicate_groups=2,
            duplicate_files=4, wasted_bytes=100, duration_ms=50,
        )
        d = s.to_dict()
        assert d == {
            "total_files": 10,
            "duplicate_groups": 2,
            "duplicate_files": 4,
            "wasted_bytes": 100,
            "duration_ms": 50,
        }


# ====================================================================
# Deduper.find_duplicates（W2 兼容 API）
# ====================================================================


class TestDeduperFindDuplicates:
    def test_no_duplicates(self, dup_tree: Path) -> None:
        # 把 "same" 们清掉，只留 a/b
        (dup_tree / "c.txt").unlink()
        (dup_tree / "d.txt").unlink()
        (dup_tree / "e.txt").unlink()
        (dup_tree / "nested" / "x.txt").unlink()

        dedup = Deduper(algorithm="md5")
        groups = dedup.find_duplicates(list(dup_tree.glob("**/*.txt")))
        assert groups == []

    def test_finds_one_group(self, dup_tree: Path) -> None:
        dedup = Deduper(algorithm="md5")
        groups = dedup.find_duplicates(list(dup_tree.glob("**/*.txt")))
        assert len(groups) == 1
        # 4 个 "same" 应当成 1 组
        assert groups[0].count == 4

    def test_algorithm_md5(self, dup_tree: Path) -> None:
        dedup = Deduper(algorithm="md5")
        groups = dedup.find_duplicates(list(dup_tree.glob("**/*.txt")))
        # md5 长度 32 hex
        assert len(groups[0].hash_value) == 32
        assert groups[0].algorithm == "md5"

    def test_algorithm_sha1(self, dup_tree: Path) -> None:
        dedup = Deduper(algorithm="sha1")
        groups = dedup.find_duplicates(list(dup_tree.glob("**/*.txt")))
        # sha1 长度 40 hex
        assert len(groups[0].hash_value) == 40
        assert groups[0].algorithm == "sha1"

    def test_algorithm_sha256(self, dup_tree: Path) -> None:
        dedup = Deduper(algorithm="sha256")
        groups = dedup.find_duplicates(list(dup_tree.glob("**/*.txt")))
        # sha256 长度 64 hex
        assert len(groups[0].hash_value) == 64
        assert groups[0].algorithm == "sha256"

    def test_sorted_by_count_desc(self, tmp_path: Path) -> None:
        # 大组 5 个 + 小组 2 个
        for i in range(5):
            (tmp_path / f"big_{i}.bin").write_bytes(b"big content")
        (tmp_path / "small_a.bin").write_bytes(b"small")
        (tmp_path / "small_b.bin").write_bytes(b"small")

        dedup = Deduper(algorithm="md5")
        groups = dedup.find_duplicates(list(tmp_path.glob("*.bin")))
        assert len(groups) == 2
        assert groups[0].count == 5  # 大组在前
        assert groups[1].count == 2

    def test_skips_non_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("hello")  # 同 a
        # tmp_path 本身是目录
        dedup = Deduper(algorithm="md5")
        groups = dedup.find_duplicates([tmp_path, tmp_path / "a.txt", tmp_path / "b.txt"])
        assert len(groups) == 1
        assert groups[0].count == 2


# ====================================================================
# Deduper.get_stats（W2 兼容 API）
# ====================================================================


class TestDeduperGetStats:
    def test_no_dup_stats(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("alpha")
        (tmp_path / "b.txt").write_text("beta")
        dedup = Deduper(algorithm="md5")
        stats = dedup.get_stats(list(tmp_path.glob("*.txt")))
        assert stats["total_files"] == 2
        assert stats["duplicates"] == 0
        assert stats["wasted_bytes"] == 0
        assert stats["savings_bytes"] == 0

    def test_with_dups(self, dup_tree: Path) -> None:
        # 4 个 "same"（4 bytes each）→ 1 组, 3 个重复, 浪费 12 字节
        dedup = Deduper(algorithm="md5")
        stats = dedup.get_stats(list(dup_tree.glob("**/*.txt")))
        assert stats["total_files"] == 6
        assert stats["duplicates"] == 3  # 4 - 1 = 3 可清理
        assert stats["wasted_bytes"] == 12
        assert stats["savings_bytes"] == 12


# ====================================================================
# Deduper.find_duplicates_with_meta（W4 v3 新 API）
# ====================================================================


class TestDeduperFindDuplicatesWithMeta:
    def test_returns_groups_and_stats(self, dup_tree: Path) -> None:
        dedup = Deduper(algorithm="md5")
        _groups, stats = dedup.find_duplicates_with_meta(
            list(dup_tree.glob("**/*.txt"))
        )
        assert isinstance(stats, DedupStats)
        assert stats.total_files == 6
        assert stats.duplicate_groups == 1
        assert stats.duplicate_files == 3
        assert stats.wasted_bytes == 12
        assert stats.duration_ms >= 0

    def test_groups_have_files_with_meta(self, dup_tree: Path) -> None:
        dedup = Deduper(algorithm="md5")
        groups, _ = dedup.find_duplicates_with_meta(
            list(dup_tree.glob("**/*.txt"))
        )
        assert len(groups) == 1
        g = groups[0]
        assert g.count == 4
        assert len(g.files_with_meta) == 4
        for m in g.files_with_meta:
            assert isinstance(m, DuplicateFile)
            assert m.size == 4  # "same" → 4 bytes
            assert m.mtime > 0
            assert m.ctime > 0

    def test_groups_have_wasted_bytes(self, dup_tree: Path) -> None:
        dedup = Deduper(algorithm="md5")
        groups, _ = dedup.find_duplicates_with_meta(
            list(dup_tree.glob("**/*.txt"))
        )
        g = groups[0]
        # hash_size * (count - 1) = 4 * 3 = 12
        assert g.hash_size == 4
        assert g.wasted_bytes == 12

    def test_groups_sorted_by_wasted_desc(self, tmp_path: Path) -> None:
        # 大组 5×100 bytes vs 小组 2×10 bytes
        for i in range(5):
            (tmp_path / f"big_{i}.bin").write_bytes(b"x" * 100)
        (tmp_path / "small_a.bin").write_bytes(b"y" * 10)
        (tmp_path / "small_b.bin").write_bytes(b"y" * 10)

        dedup = Deduper(algorithm="md5")
        groups, _ = dedup.find_duplicates_with_meta(
            list(tmp_path.glob("*.bin"))
        )
        # 大组浪费 400 bytes 排前
        assert groups[0].wasted_bytes == 400
        assert groups[0].count == 5
        assert groups[1].wasted_bytes == 10
        assert groups[1].count == 2

    def test_no_duplicates_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("alpha")
        (tmp_path / "b.txt").write_text("beta")
        dedup = Deduper(algorithm="md5")
        groups, stats = dedup.find_duplicates_with_meta(
            list(tmp_path.glob("*.txt"))
        )
        assert groups == []
        assert stats.total_files == 2
        assert stats.duplicate_groups == 0
        assert stats.duplicate_files == 0
        assert stats.wasted_bytes == 0

    def test_metadata_missing_file_safe(self, tmp_path: Path) -> None:
        # 创建两个相同文件，然后删掉一个 → stat 失败兜底
        (tmp_path / "a.txt").write_text("same")
        p_b = tmp_path / "b.txt"
        p_b.write_text("same")
        p_b.unlink()  # 删掉

        dedup = Deduper(algorithm="md5")
        # 只剩 1 个文件, 没有重复
        groups, _ = dedup.find_duplicates_with_meta([tmp_path / "a.txt"])
        assert groups == []


# ====================================================================
# _stat_safe
# ====================================================================


class TestStatSafe:
    def test_real_file(self, tmp_path: Path) -> None:
        p = tmp_path / "x.txt"
        p.write_text("hello")
        st = _stat_safe(p)
        assert st is not None
        assert st.st_size == 5

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        st = _stat_safe(tmp_path / "nope.txt")
        assert st is None

    def test_directory(self, tmp_path: Path) -> None:
        st = _stat_safe(tmp_path)
        assert st is not None
        assert (st.st_mode & 0o170000) == 0o040000  # 目录


# ====================================================================
# find_duplicates_in_dir（同步入口）
# ====================================================================


class TestFindDuplicatesInDir:
    def test_recursive(self, dup_tree: Path) -> None:
        _groups, stats = find_duplicates_in_dir(dup_tree, recursive=True)
        assert stats.total_files == 6
        assert stats.duplicate_groups == 1
        assert stats.duplicate_files == 3

    def test_non_recursive(self, dup_tree: Path) -> None:
        _groups, stats = find_duplicates_in_dir(dup_tree, recursive=False)
        # 不递归 → 5 个顶层文件 (a/b/c/d/e)
        assert stats.total_files == 5
        # c/d/e 同 hash → 1 组, 2 重复
        assert stats.duplicate_groups == 1
        assert stats.duplicate_files == 2

    def test_sha256_algorithm(self, dup_tree: Path) -> None:
        groups, _ = find_duplicates_in_dir(dup_tree, algorithm="sha256")
        assert groups[0].algorithm == "sha256"
        assert len(groups[0].hash_value) == 64

    def test_empty_dir(self, tmp_path: Path) -> None:
        groups, stats = find_duplicates_in_dir(tmp_path)
        assert groups == []
        assert stats.total_files == 0
        assert stats.duplicate_groups == 0

    def test_deduper_reuse(self, dup_tree: Path) -> None:
        # 同一 Deduper 实例可以反复调用
        deduper = Deduper(algorithm="md5")
        g1, _ = deduper.find_duplicates_with_meta(
            list(dup_tree.glob("*.txt"))
        )
        g2, _ = deduper.find_duplicates_with_meta(
            list(dup_tree.glob("**/*.txt"))
        )
        # 第二种跑全部 → 1 组
        assert len(g2) == 1
        # 第一种只跑顶层 → 1 组（c/d/e）
        assert len(g1) == 1
