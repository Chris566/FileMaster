"""pytest 全局 fixtures.

W1 阶段：构造 tmp 目录、文件工厂、空配置对象。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# CI/headless 环境自动设 offscreen (无 X11 display 的 runner 会崩)
# Linux/macOS 都需要;Windows 自动忽略(不影响)
if sys.platform.startswith(("linux", "darwin")) and not os.environ.get("QT_QPA_PLATFORM"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """临时目录."""
    return tmp_path


@pytest.fixture
def make_files(tmp_path: Path):
    """工厂 fixture：批量创建测试文件.

    用法:
        def test_x(make_files):
            files = make_files(count=5, prefix="doc", ext=".txt", content=b"hello")
    """

    def _make(
        count: int = 3,
        prefix: str = "file",
        ext: str = ".txt",
        content: bytes = b"test content",
        subdir: str | None = None,
    ) -> list[Path]:
        root = tmp_path / subdir if subdir else tmp_path
        root.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        for i in range(1, count + 1):
            f = root / f"{prefix}_{i:03d}{ext}"
            f.write_bytes(content)
            files.append(f)
        return files

    return _make


@pytest.fixture
def sample_files(make_files) -> list[Path]:
    """3 个标准样本文件."""
    return make_files(count=3, prefix="doc", ext=".pdf", content=b"%PDF-1.4\nfake pdf content")


@pytest.fixture
def mixed_files(tmp_path: Path) -> dict[str, list[Path]]:
    """混合类型文件（PDF/Word/Excel/PPT/Image）."""
    result: dict[str, list[Path]] = {}
    types = {
        "PDF": (".pdf", 3),
        "WORD": (".docx", 2),
        "EXCEL": (".xlsx", 2),
        "PPT": (".pptx", 1),
        "IMAGE": (".png", 4),
    }
    for cat, (ext, n) in types.items():
        files = []
        for i in range(1, n + 1):
            f = tmp_path / f"{cat.lower()}_{i}{ext}"
            f.write_bytes(b"\x00" * 100)
            files.append(f)
        result[cat] = files
    return result


@pytest.fixture(autouse=True)
def reset_env(monkeypatch, tmp_path):
    """每个测试前清空 XDG / APPDATA，强制用临时目录."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    yield


@pytest.fixture
def gui_app(qtbot):
    """Qt 应用 fixture（pytest-qt）."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
