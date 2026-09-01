"""文件哈希工具测试."""

from __future__ import annotations

import pytest

from filemaster.utils.hash import file_hash


class TestFileHash:
    """file_hash() 测试."""

    def test_md5_basic(self, tmp_path) -> None:
        f = tmp_path / "x.txt"
        f.write_bytes(b"hello")
        h = file_hash(f, "md5")
        # MD5("hello") = 5d41402abc4b2a76b9719d911017c592
        assert h == "5d41402abc4b2a76b9719d911017c592"

    def test_sha1_basic(self, tmp_path) -> None:
        f = tmp_path / "x.txt"
        f.write_bytes(b"hello")
        h = file_hash(f, "sha1")
        assert len(h) == 40  # SHA1 hex = 40 chars

    def test_sha256_basic(self, tmp_path) -> None:
        f = tmp_path / "x.txt"
        f.write_bytes(b"hello")
        h = file_hash(f, "sha256")
        assert len(h) == 64  # SHA256 hex = 64 chars

    def test_blake2b_basic(self, tmp_path) -> None:
        f = tmp_path / "x.txt"
        f.write_bytes(b"hello")
        h = file_hash(f, "blake2b")
        assert len(h) == 128  # BLAKE2b hex = 128 chars

    def test_unknown_algorithm_raises(self, tmp_path) -> None:
        f = tmp_path / "x.txt"
        f.write_bytes(b"x")
        with pytest.raises(ValueError, match="不支持的算法"):
            file_hash(f, "fakealgo")

    def test_missing_file_raises(self, tmp_path) -> None:
        f = tmp_path / "missing.txt"
        with pytest.raises(FileNotFoundError):
            file_hash(f)

    def test_same_content_same_hash(self, tmp_path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"same content")
        f2.write_bytes(b"same content")
        assert file_hash(f1) == file_hash(f2)

    def test_different_content_different_hash(self, tmp_path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")
        assert file_hash(f1) != file_hash(f2)

    def test_case_insensitive_algorithm(self, tmp_path) -> None:
        f = tmp_path / "x.txt"
        f.write_bytes(b"x")
        assert file_hash(f, "MD5") == file_hash(f, "md5")

    # ---- W9: 硬中断 (分块检查 is_cancelled) ----

    def test_is_cancelled_none_works(self, tmp_path) -> None:
        """W9: 不传 is_cancelled 时行为完全兼容 (W5/W6/W7)."""
        f = tmp_path / "x.txt"
        f.write_bytes(b"hello")
        h = file_hash(f, "md5", is_cancelled=None)
        assert h == "5d41402abc4b2a76b9719d911017c592"

    def test_is_cancelled_always_false_runs_full(self, tmp_path) -> None:
        """W9: is_cancelled 永远 False, 正常完成."""
        f = tmp_path / "x.txt"
        f.write_bytes(b"hello")
        h = file_hash(f, "md5", is_cancelled=lambda: False)
        assert h == "5d41402abc4b2a76b9719d911017c592"

    def test_is_cancelled_immediate_raises(self, tmp_path) -> None:
        """W9: 立即 cancel → 第一块后抛 HashCancelledError."""
        from filemaster.utils.hash import HashCancelledError

        f = tmp_path / "x.txt"
        f.write_bytes(b"hello world")
        with pytest.raises(HashCancelledError) as exc_info:
            file_hash(f, "md5", is_cancelled=lambda: True)
        assert exc_info.value.file == f
