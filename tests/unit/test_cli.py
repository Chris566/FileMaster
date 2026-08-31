"""CLI 集成测试（W4 v1：classify 子命令）.

走 subprocess 跑真实 filemaster CLI，端到端验证：
- --json 输出格式
- --group 分组
- --copy 复制到类别子目录
- --move 移动 + dry-run
- 错误处理（不存在源路径）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _project_root() -> Path:
    """从本 test 文件位置向上找含 pyproject.toml 的项目根.

    CI runner（Linux / Windows）和本地开发机都能用——不依赖任何绝对路径。
    """
    p = Path(__file__).resolve()
    for parent in (p, *p.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError(
        "filemaster 项目根未找到——预期在 tests/unit/ 上方能找到含 pyproject.toml 的目录"
    )


PROJECT_ROOT = _project_root()


def _run(*args: str, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """跑 filemaster CLI 同步.

    encoding="utf-8"：显式按 UTF-8 解码子进程 stdout/stderr——
    Windows GitHub Actions runner 默认 locale 是 cp1252，emoji/中文输出不显式指定会解码错。
    errors="replace"：真解码不了的字符用 ? 替换，不抛 UnicodeDecodeError。
    env: 显式传 env 时走指定 env (测试要改 HOME 隔离 undo log 时用);
        默认 None 走当前进程 os.environ 拷贝, 兼容其它测试.
    """
    return subprocess.run(
        [sys.executable, "-m", "filemaster.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd or PROJECT_ROOT,
        env=env if env is not None else None,
        timeout=30,
    )


def _make_pdf(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    return f


def _make_png(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    return f


def _make_py(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.py"
    f.write_text("x = 1\n")
    return f


class TestClassifyList:
    """默认 list 模式."""

    def test_classify_basic(self, tmp_path: Path) -> None:
        _make_pdf(tmp_path, "a")
        _make_png(tmp_path, "b")
        _make_py(tmp_path, "c")
        r = _run("classify", "-s", str(tmp_path))
        assert r.returncode == 0
        assert "📁 分类结果" in r.stdout
        assert "PDF" in r.stdout
        assert "IMAGE" in r.stdout
        assert "CODE" in r.stdout

    def test_classify_recursive(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        _make_pdf(sub, "a")
        r = _run("classify", "-s", str(tmp_path), "-r")
        assert r.returncode == 0
        assert "PDF" in r.stdout

    def test_classify_nonexistent_source(self, tmp_path: Path) -> None:
        r = _run("classify", "-s", str(tmp_path / "ghost"))
        assert r.returncode == 1
        assert "不存在" in r.stderr

    def test_classify_empty_dir(self, tmp_path: Path) -> None:
        r = _run("classify", "-s", str(tmp_path))
        assert r.returncode == 0
        assert "无文件" in r.stdout


class TestClassifyJSON:
    """--json 输出."""

    def test_json_output(self, tmp_path: Path) -> None:
        _make_pdf(tmp_path, "a")
        _make_png(tmp_path, "b")
        r = _run("classify", "-s", str(tmp_path), "--json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "total" in data
        assert data["total"] == 2
        assert "items" in data
        assert "summary" in data
        assert data["summary"]["PDF"] == 1
        assert data["summary"]["IMAGE"] == 1

    def test_json_item_fields(self, tmp_path: Path) -> None:
        _make_pdf(tmp_path, "doc")
        r = _run("classify", "-s", str(tmp_path), "--json")
        data = json.loads(r.stdout)
        item = data["items"][0]
        assert item["category"] == "PDF"
        assert item["category_zh"] == "PDF"
        assert "confidence" in item
        assert "method" in item
        assert "mime_type" in item


class TestClassifyGroup:
    """--group / --by-category."""

    def test_group_output(self, tmp_path: Path) -> None:
        _make_pdf(tmp_path, "a")
        _make_pdf(tmp_path, "b")
        _make_png(tmp_path, "c")
        r = _run("classify", "-s", str(tmp_path), "--group")
        assert r.returncode == 0
        assert "PDF" in r.stdout
        assert "IMAGE" in r.stdout
        assert "2 个文件" in r.stdout
        assert "1 个文件" in r.stdout

    def test_by_category(self, tmp_path: Path) -> None:
        _make_pdf(tmp_path, "doc1")
        r = _run("classify", "-s", str(tmp_path), "--by-category")
        assert r.returncode == 0
        assert "doc1" in r.stdout


class TestClassifyCopy:
    """--copy 复制."""

    def test_copy_to_category_dirs(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        _make_pdf(src, "a")
        _make_png(src, "b")
        r = _run("classify", "-s", str(src), "--copy", str(dst))
        assert r.returncode == 0
        assert (dst / "PDF" / "a.pdf").exists()
        assert (dst / "IMAGE" / "b.png").exists()
        # 源文件保留
        assert (src / "a.pdf").exists()

    def test_copy_dry_run(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        _make_pdf(src, "a")
        r = _run("classify", "-s", str(src), "--copy", str(dst), "--dry-run")
        assert r.returncode == 0
        assert "dry-run" in r.stdout
        assert not (dst / "PDF").exists()


class TestClassifyMove:
    """--move 移动."""

    def test_move_removes_source(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        _make_pdf(src, "a")
        r = _run("classify", "-s", str(src), "--move", str(dst))
        assert r.returncode == 0
        assert (dst / "PDF" / "a.pdf").exists()
        assert not (src / "a.pdf").exists()

    def test_move_dry_run_keeps_source(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        _make_pdf(src, "a")
        r = _run("classify", "-s", str(src), "--move", str(dst), "--dry-run")
        assert r.returncode == 0
        assert (src / "a.pdf").exists()
        assert not (dst / "PDF").exists()


class TestHelp:
    """help 与无子命令."""

    def test_no_command_shows_help(self) -> None:
        r = _run()
        assert r.returncode == 0
        assert "usage" in r.stdout.lower() or "usage" in r.stderr.lower()

    def test_classify_help(self) -> None:
        r = _run("classify", "--help")
        assert r.returncode == 0
        assert "--json" in r.stdout
        assert "--copy" in r.stdout
        assert "--move" in r.stdout


# ============================================================
# W4 v5: dedup-undo CLI
# ============================================================


class TestDedupUndoCLI:
    """dedup-undo 子命令 (list + restore) — 走真实 subprocess."""

    def test_dedup_undo_list_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空目录 → 提示没有, 返 0."""
        env = {**os.environ, "HOME": str(tmp_path)}
        r = _run("dedup-undo", "list", env=env)
        assert r.returncode == 0
        assert "没有 undo log" in r.stdout

    def test_dedup_undo_list_with_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """有一个 log → 列出."""
        undo_dir = tmp_path / ".filemaster" / "undo"
        undo_dir.mkdir(parents=True)
        (undo_dir / "20260831_120000_abc12345_move.json").write_text(
            json.dumps({
                "action": "move", "timestamp": "t", "group_hash": "abc12345", "keeper": "/k",
                "entries": [{"op": "move", "from": "/src", "to": "/dst"}],
            }),
            encoding="utf-8",
        )
        env = {**os.environ, "HOME": str(tmp_path)}
        r = _run("dedup-undo", "list", env=env)
        assert r.returncode == 0
        assert "找到 1 个 undo log" in r.stdout
        assert "abc12345" in r.stdout
        assert "可恢复" in r.stdout

    def test_dedup_undo_restore_dry_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dry-run 模式 → 文件不变."""
        work = tmp_path / "work"
        work.mkdir()
        keeper = work / "k.txt"
        keeper.write_text("k")
        dup = work / "dup" / "k.txt"
        dup.parent.mkdir()
        dup.write_text("k")
        original = work / "original.txt"  # 还没创建
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps({
            "action": "move", "timestamp": "t", "group_hash": "h", "keeper": str(keeper),
            "entries": [{"op": "move", "from": str(dup), "to": str(original)}],
        }), encoding="utf-8")
        env = {**os.environ, "HOME": str(tmp_path)}

        r = _run("dedup-undo", "restore", str(log_path), "--dry-run", env=env)
        assert r.returncode == 0
        assert "DRY-RUN" in r.stdout
        # 文件没动
        assert dup.exists()
        assert not original.exists()

    def test_dedup_undo_restore_real(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """真恢复 → 文件移回原位."""
        work = tmp_path / "work"
        work.mkdir()
        keeper = work / "k.txt"
        keeper.write_text("k")
        dup = work / "dup" / "k.txt"
        dup.parent.mkdir()
        dup.write_text("NEW")
        original = work / "original.txt"
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps({
            "action": "move", "timestamp": "t", "group_hash": "h", "keeper": str(keeper),
            "entries": [{"op": "move", "from": str(dup), "to": str(original)}],
        }), encoding="utf-8")
        env = {**os.environ, "HOME": str(tmp_path)}

        r = _run("dedup-undo", "restore", str(log_path), env=env)
        assert r.returncode == 0
        assert original.exists()
        assert original.read_text() == "NEW"
        assert not dup.exists()

    def test_dedup_undo_restore_missing_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """log 不存在 → 返 1."""
        env = {**os.environ, "HOME": str(tmp_path)}
        r = _run("dedup-undo", "restore", str(tmp_path / "no_such.json"), env=env)
        assert r.returncode == 1
        assert "不存在" in r.stdout

    def test_dedup_undo_restore_delete_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """delete action 不可恢复 → 返 1."""
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps({
            "action": "delete", "timestamp": "t", "group_hash": "h", "keeper": "/k",
            "entries": [{"op": "delete", "path": "/a"}],
        }), encoding="utf-8")
        env = {**os.environ, "HOME": str(tmp_path)}
        r = _run("dedup-undo", "restore", str(log_path), env=env)
        assert r.returncode == 1
        assert "不可恢复" in r.stdout
