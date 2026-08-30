"""平台路径工具测试."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from filemaster.platform.paths import (
    LONG_PATH_PREFIX,
    LONG_PATH_THRESHOLD,
    LONG_UNC_PREFIX,
    get_persist_root,
    normalize_long_path,
)


class TestNormalizeLongPath:
    """长路径处理."""

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_short_path_unchanged(self) -> None:
        p = Path("C:\\Users\\test\\file.txt")
        result = normalize_long_path(p)
        assert "\\?\\" not in str(result)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_long_path_gets_prefix(self) -> None:
        long_path = "C:\\" + "very_long_dir_name\\" * 20 + "file.txt"
        p = Path(long_path)
        assert len(str(p)) > LONG_PATH_THRESHOLD
        result = normalize_long_path(p)
        assert str(result).startswith(LONG_PATH_PREFIX)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_unc_path_uses_unc_prefix(self) -> None:
        long_unc = "\\\\server\\share\\" + "long\\" * 30 + "file.txt"
        p = Path(long_unc)
        if len(str(p)) > LONG_PATH_THRESHOLD:
            result = normalize_long_path(p)
            assert str(result).startswith(LONG_UNC_PREFIX)

    def test_non_windows_returns_path(self) -> None:
        if sys.platform == "win32":
            pytest.skip("Unix only")
        p = Path("/tmp/very/long/path/" + "x" * 300)
        result = normalize_long_path(p)
        assert isinstance(result, Path)


class TestGetPersistRoot:
    """持久化根目录."""

    def test_returns_path(self) -> None:
        d = get_persist_root()
        assert isinstance(d, Path)
        # 路径必须以 filemaster 或 FileMaster 结尾
        assert d.name in ("FileMaster", "filemaster")

    def test_uses_appdata_on_windows(self, monkeypatch) -> None:
        """Windows 下使用 %APPDATA%."""
        if sys.platform != "win32":
            pytest.skip("Windows only")
        fake_appdata = "C:\\Users\\test\\AppData\\Roaming"
        monkeypatch.setenv("APPDATA", fake_appdata)
        d = get_persist_root()
        assert str(d).startswith(fake_appdata)

    def test_uses_xdg_on_linux(self, monkeypatch) -> None:
        """Linux 下使用 XDG_DATA_HOME."""
        if sys.platform == "win32":
            pytest.skip("Linux only")
        fake_xdg = "/tmp/fake_xdg"
        monkeypatch.setenv("XDG_DATA_HOME", fake_xdg)
        d = get_persist_root()
        assert str(d).startswith(fake_xdg)
        assert d.name == "filemaster"
