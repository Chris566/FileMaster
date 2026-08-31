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

    encoding="utf-8": 显式按 UTF-8 解码子进程 stdout/stderr——
    Windows GitHub Actions runner 默认 locale 是 cp1252, emoji/中文输出不显式指定会解码错。
    errors="replace": 真解码不了的字符用 ? 替换, 不抛 UnicodeDecodeError。
    env: 显式传 env 时走指定 env (测试要改 HOME 隔离 undo log 时用);
        默认 None 走当前进程 os.environ 拷贝, 兼容其它测试.

    Windows 兼容: env 里有 HOME 时自动同步设 USERPROFILE/HOMEDRIVE/HOMEPATH
    (Python on Windows 的 ntpath.expanduser 只看 USERPROFILE, 不看 HOME).
    """
    if env is not None and "HOME" in env and sys.platform == "win32":
        env = dict(env)  # 不修改原 dict
        home = env["HOME"]
        # 必须用直接赋值, 不能用 setdefault —
        # os.environ 在 Windows CI runner 上已有 USERPROFILE=C:\Users\xxx,
        # setdefault 不会覆盖, subprocess 的 Path.home() 仍走真 user home,
        # dedup-undo 找不到 test 造的 tmp_path log → 报 "没有 undo log".
        env["USERPROFILE"] = home
        # HOMEDRIVE 是 C: 之类, HOMEPATH 是 \Users\foo
        # 简单起见, HOMEDRIVE 清空, HOMEPATH 用 tmp_path 绝对路径
        env["HOMEDRIVE"] = ""
        env["HOMEPATH"] = home.replace("C:", "", 1) if home.upper().startswith("C:") else home
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



# ============================================================
# W5: rename CLI 子命令测试
# ============================================================


class TestRenameBasic:
    def test_rename_dry_run_keeps_files(self, tmp_path: Path) -> None:
        """--dry-run 不动文件."""
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        r = _run("rename", "-s", str(tmp_path), "-t", "{Index:D3}_{OriginalName}", "--dry-run")
        assert r.returncode == 0
        assert f.exists()  # 原文件还在
        assert not (tmp_path / "001_doc.txt").exists()

    def test_rename_real_exec(self, tmp_path: Path) -> None:
        """真执行, 旧文件消失, 新文件存在."""
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        r = _run("rename", "-s", str(tmp_path), "-t", "{Index:D3}_{OriginalName}")
        assert r.returncode == 0
        assert not f.exists()
        assert (tmp_path / "001_doc.txt").exists()

    def test_rename_with_prefix(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        r = _run("rename", "-s", str(tmp_path), "-t", "{Prefix}{Index}_{OriginalName}", "-p", "pre_")
        assert r.returncode == 0
        assert (tmp_path / "pre_1_doc.txt").exists()

    def test_rename_index_padding(self, tmp_path: Path) -> None:
        files = []
        for i in range(3):
            f = tmp_path / f"f{i}.txt"
            f.write_text("x")
            files.append(f)
        r = _run("rename", "-s", str(tmp_path), "-t", "{Index:D3}_{OriginalName}")
        assert r.returncode == 0
        # 排序后第一个是 f0.txt, 应改成 001_f0.txt
        assert (tmp_path / "001_f0.txt").exists()
        assert (tmp_path / "002_f1.txt").exists()
        assert (tmp_path / "003_f2.txt").exists()

    def test_rename_recursive(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        f1 = tmp_path / "a.txt"
        f1.write_text("x")
        f2 = sub / "b.txt"
        f2.write_text("x")
        r = _run("rename", "-s", str(tmp_path), "-t", "{Index:D3}_{OriginalName}", "-r")
        assert r.returncode == 0
        assert (tmp_path / "001_a.txt").exists()
        assert (sub / "002_b.txt").exists()


class TestRenameConflict:
    def test_rename_collision_skip_default(self, tmp_path: Path) -> None:
        """冲突默认 skip, 目标已存在则不覆盖.

        W5 修复: 用单文件源 (W5 CLI 支持 -s 直接接受单文件), 避免
        dir scan 把冲突目标 001_doc.txt 也一起捞进来处理.
        """
        f = tmp_path / "doc.txt"
        f.write_text("new")
        # 提前建一个 001_doc.txt (冲突目标)
        (tmp_path / "001_doc.txt").write_text("existing")
        r = _run("rename", "-s", str(f), "-t", "{Index:D3}_{OriginalName}")
        assert r.returncode == 0
        # 原文件保留 (skip 策略)
        assert f.exists()
        # 目标文件内容不变
        assert (tmp_path / "001_doc.txt").read_text() == "existing"

    def test_rename_collision_overwrite(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("new")
        (tmp_path / "001_doc.txt").write_text("existing")
        r = _run(
            "rename", "-s", str(f),
            "-t", "{Index:D3}_{OriginalName}",
            "--conflict", "overwrite",
        )
        assert r.returncode == 0
        assert not f.exists()
        assert (tmp_path / "001_doc.txt").read_text() == "new"

    def test_rename_collision_rename_new(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("new")
        (tmp_path / "001_doc.txt").write_text("existing")
        r = _run(
            "rename", "-s", str(f),
            "-t", "{Index:D3}_{OriginalName}",
            "--conflict", "rename_new",
        )
        assert r.returncode == 0
        assert not f.exists()
        # rename_new 在 stem 后追加 (1), target=001_doc.txt → 新名 001_doc (1).txt
        assert (tmp_path / "001_doc (1).txt").exists()
        # 原冲突目标保留
        assert (tmp_path / "001_doc.txt").exists()


class TestRenameOutput:
    def test_rename_json_output(self, tmp_path: Path) -> None:
        """W5: --json + --dry-run 模式输出纯 JSON, 进度条不打."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        r = _run(
            "rename", "-s", str(tmp_path),
            "-t", "{Index:D3}_{OriginalName}",
            "--dry-run", "--json",
        )
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "stats" in data
        assert "items" in data
        assert data["stats"]["total"] == 1
        assert data["mode"] == "dry-run"
        # 进度回调在 JSON 模式不输出, stdout 应是纯 JSON
        assert r.stdout.startswith("{")

    def test_rename_human_output(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("x")
        r = _run(
            "rename", "-s", str(tmp_path),
            "-t", "{Index:D3}_{OriginalName}",
        )
        assert r.returncode == 0
        # 人类可读输出应含 📊 完成:
        assert "📊 完成" in r.stdout or "完成:" in r.stdout


class TestRenameErrors:
    def test_rename_nonexistent_source(self, tmp_path: Path) -> None:
        r = _run("rename", "-s", str(tmp_path / "no_such"), "-t", "{OriginalName}")
        assert r.returncode == 1
        # 错误信息走 stderr (跟 grep/dedup CLI 一致)
        assert "不存在" in r.stderr

    def test_rename_empty_dir(self, tmp_path: Path) -> None:
        r = _run("rename", "-s", str(tmp_path), "-t", "{OriginalName}")
        assert r.returncode == 0
        assert "无文件" in r.stdout

    def test_rename_invalid_conflict(self, tmp_path: Path) -> None:
        """argparse 会直接拦截非法 --conflict 值, returncode=2."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        r = _run(
            "rename", "-s", str(tmp_path),
            "-t", "{OriginalName}",
            "--conflict", "wrong_value",
        )
        assert r.returncode == 2
        assert "invalid choice" in r.stderr


class TestRenameNamespacedPlaceholder:
    def test_rename_pdf_namespace(self, tmp_path: Path) -> None:
        """W5: {pdf_title} 占位符端到端."""
        try:
            import fitz
        except ImportError:
            pytest.skip("PyMuPDF not installed")
        p = tmp_path / "report.pdf"
        import fitz
        doc = fitz.open()
        doc.new_page()
        doc.set_metadata({"title": "Annual"})
        doc.save(p)
        doc.close()

        r = _run("rename", "-s", str(tmp_path), "-t", "{pdf_title}_{OriginalName}")
        assert r.returncode == 0
        assert (tmp_path / "Annual_report.pdf").exists()
        assert not p.exists()

    def test_rename_image_aspect_ratio(self, tmp_path: Path) -> None:
        """W5: {image_aspect_ratio} 占位符端到端."""
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")
        p = tmp_path / "img.png"
        from PIL import Image
        Image.new("RGB", (1920, 1080), "red").save(p)

        r = _run("rename", "-s", str(tmp_path), "-t", "{image_aspect_ratio}_{OriginalName}")
        assert r.returncode == 0
        # ":" 被 renamer.sanitize 替换成 "_" (Windows 非法字符)
        assert (tmp_path / "16_9_img.png").exists()
