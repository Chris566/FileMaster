"""W9: safe_rename 硬中断安全测试."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from filemaster.core.safe_rename import (
    SafeRenameResult,
    cleanup_orphan_tmps,
    find_orphan_tmps,
    make_tmp_path,
    safe_rename,
)

# ---------- make_tmp_path ----------

class TestMakeTmpPath:
    def test_creates_filemaster_tmp_suffix(self, tmp_path: Path) -> None:
        src = tmp_path / "report.pdf"
        src.write_text("hi")
        tmp = make_tmp_path(src)
        # 文件名构成: 原文件名 + .filemaster.tmp. + 8 位 hex
        assert tmp.name.startswith("report.pdf.filemaster.tmp.")
        assert tmp.name.endswith(".f16a9e8e") is False  # 8 hex 字符
        # 末 8 位是 hex
        tail = tmp.name.split(".filemaster.tmp.")[-1]
        assert len(tail) == 8
        assert all(c in "0123456789abcdef" for c in tail)
        # 整长: 原名 + .filemaster.tmp.(18) + 8
        assert tmp.name == "report.pdf" + ".filemaster.tmp." + tail
        # tmp 跟 src 在同目录
        assert tmp.parent == src.parent
        # 末 8 位在不同内容/时间下应稳定(md5 相同输入)
        assert make_tmp_path(src) == make_tmp_path(src)

    def test_same_file_same_tmp_path(self, tmp_path: Path) -> None:
        """同源文件两次调用得相同 tmp (避免重复创建)."""
        src = tmp_path / "doc.txt"
        src.write_text("x")
        assert make_tmp_path(src) == make_tmp_path(src)

    def test_different_files_different_tmp(self, tmp_path: Path) -> None:
        """不同文件(不同名/不同内容)得不同 tmp (防覆盖残留)."""
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("first")
        time.sleep(0.01)
        b.write_text("second")
        # 不同名/不同 inode/不同 mtime → 不同 tmp
        assert make_tmp_path(a) != make_tmp_path(b)

    def test_overwrite_same_path_different_mtime(self, tmp_path: Path) -> None:
        """同名覆盖后 inode/mtime 变了 tmp 也变."""
        a = tmp_path / "a.txt"
        a.write_text("first")
        first_tmp = make_tmp_path(a)
        time.sleep(0.01)
        a.write_text("second")  # 覆盖同路径, 内容/大小/mtime 全变
        second_tmp = make_tmp_path(a)
        assert first_tmp != second_tmp


# ---------- safe_rename 正常路径 ----------

class TestSafeRenameNormal:
    def test_no_cancel_renames_file(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("hello")
        dst = tmp_path / "dst.txt"

        result = safe_rename(src, dst)

        assert result.status == "OK"
        assert result.source == src
        assert result.target == dst
        assert dst.read_text() == "hello"
        assert not src.exists()
        # 无残留 tmp
        assert list(tmp_path.glob("*.filemaster.tmp.*")) == []

    def test_dst_already_exists_overwrites(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("new")
        dst.write_text("old")

        result = safe_rename(src, dst)

        assert result.status == "OK"
        assert dst.read_text() == "new"
        assert not src.exists()

    def test_no_cancel_token_skips_check(self, tmp_path: Path) -> None:
        """不传 is_cancelled → 走原 W5/W6 行为."""
        src = tmp_path / "a.txt"
        dst = tmp_path / "b.txt"
        src.write_text("x")

        result = safe_rename(src, dst, is_cancelled=None)

        assert result.status == "OK"
        assert dst.exists()
        assert not src.exists()

    def test_cancel_token_never_true_acts_like_none(self, tmp_path: Path) -> None:
        src = tmp_path / "a.txt"
        dst = tmp_path / "b.txt"
        src.write_text("x")

        result = safe_rename(src, dst, is_cancelled=lambda: False)

        assert result.status == "OK"
        assert dst.exists()
        assert not src.exists()


# ---------- safe_rename 取消路径 (W9 核心) ----------

class TestSafeRenameCancelled:
    def test_cancel_before_step_a_returns_error(self, tmp_path: Path) -> None:
        """源不存在 → ERROR, 不动."""
        src = tmp_path / "missing.txt"
        dst = tmp_path / "dst.txt"

        result = safe_rename(src, dst, is_cancelled=lambda: True)

        assert result.status == "ERROR"
        assert not dst.exists()
        assert "不存在" in result.message

    def test_cancel_after_step_a_rolls_back(self, tmp_path: Path) -> None:
        """Step A 完成后取消 → ROLLBACK, 源保留, 目标未创建."""
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        original = "important data"
        src.write_text(original)

        result = safe_rename(src, dst, is_cancelled=lambda: True)

        assert result.status == "ROLLBACK"
        # 源文件保留 + 内容完整
        assert src.exists()
        assert src.read_text() == original
        # 目标未创建
        assert not dst.exists()
        # 无残留 tmp
        assert list(tmp_path.glob("*.filemaster.tmp.*")) == []
        assert "已取消" in result.message or "回滚" in result.message

    def test_cancel_only_at_step_a_checkpoint(self, tmp_path: Path) -> None:
        """is_cancelled=True 触发一次后保持 True → ROLLBACK."""
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("x")

        # 标记: 第一次调用(在 Step A 后) → 取消
        call_count = {"n": 0}

        def cancel_after_a() -> bool:
            call_count["n"] += 1
            return call_count["n"] >= 1  # 第一次就 True

        result = safe_rename(src, dst, is_cancelled=cancel_after_a)

        assert result.status == "ROLLBACK"
        assert src.exists()


# ---------- safe_rename 错误路径 ----------

class TestSafeRenameErrors:
    def test_missing_source_returns_error(self, tmp_path: Path) -> None:
        src = tmp_path / "ghost.txt"
        dst = tmp_path / "dst.txt"

        result = safe_rename(src, dst)

        assert result.status == "ERROR"
        assert not dst.exists()
        assert "不存在" in result.message

    def test_dst_in_nonexistent_dir_returns_error(self, tmp_path: Path) -> None:
        src = tmp_path / "a.txt"
        src.write_text("x")
        dst = tmp_path / "no-such-dir" / "dst.txt"  # 父目录不存在

        result = safe_rename(src, dst)

        # Step A 用的是 tmp 路径(同源目录) → 应成功
        # Step B os.replace 失败(父目录不存在) → ERROR
        # 然后尝试 rollback: shutil.move(tmp, src) 应该成功
        # 终态: src 恢复原位, 无残留 tmp
        assert result.status == "ERROR"
        # 验证: 源文件回到了原位(rollback 成功)
        assert src.exists()
        assert src.read_text() == "x"
        # 验证: 无残留 tmp (rollback 干净)
        assert list(tmp_path.glob("*.filemaster.tmp.*")) == []
        # 目标确实未创建
        assert not dst.exists()


# ---------- 孤儿临时文件 ----------

class TestOrphanTmps:
    def test_find_orphan_tmps_returns_only_tmp(self, tmp_path: Path) -> None:
        a = tmp_path / "real.txt"
        a.write_text("x")
        orphan = tmp_path / "real.txt.filemaster.tmp.deadbeef"
        orphan.write_text("leftover")

        result = find_orphan_tmps(tmp_path)

        assert orphan in result
        assert a not in result

    def test_find_orphan_empty_dir(self, tmp_path: Path) -> None:
        assert find_orphan_tmps(tmp_path) == []

    def test_cleanup_orphan_tmps_removes_them(self, tmp_path: Path) -> None:
        for i in range(3):
            (tmp_path / f"f{i}.txt.filemaster.tmp.{i:08x}").write_text("x")
        (tmp_path / "normal.txt").write_text("keep")

        removed = cleanup_orphan_tmps(tmp_path)

        assert removed == 3
        assert find_orphan_tmps(tmp_path) == []
        assert (tmp_path / "normal.txt").exists()

    def test_cleanup_orphan_tmps_empty_dir(self, tmp_path: Path) -> None:
        assert cleanup_orphan_tmps(tmp_path) == 0

    def test_find_orphan_tmps_recursive(self, tmp_path: Path) -> None:
        """W9: cleanup 应该是递归的(子目录的 .tmp 也清)."""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "x.txt.filemaster.tmp.12345678").write_text("x")

        result = find_orphan_tmps(tmp_path)

        assert any(p.name == "x.txt.filemaster.tmp.12345678" for p in result)
