"""平台相关路径工具."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Windows 路径长度阈值（默认 MAX_PATH = 260）
LONG_PATH_THRESHOLD = 240
LONG_PATH_PREFIX = "\\\\?\\"
LONG_UNC_PREFIX = "\\\\?\\UNC\\"


def normalize_long_path(path: str | Path) -> Path:
    """长路径加 `\\\\?\\` 前缀（P0-2 任务）.

    Args:
        path: 原始路径
    Returns:
        归一化后的 Path 对象

    Examples:
        >>> normalize_long_path("C:\\\\short\\\\file.txt")
        WindowsPath('C:/short/file.txt')
        >>> normalize_long_path("\\\\\\\\server\\\\share" + "\\\\" + "x" * 250)
        WindowsPath('//?/UNC/server/share/xxx...')
    """
    p = Path(path)
    s = str(p)
    if sys.platform != "win32":
        return p
    if s.startswith(LONG_PATH_PREFIX) or s.startswith(LONG_UNC_PREFIX):
        return p
    if len(s) <= LONG_PATH_THRESHOLD:
        return p
    if s.startswith("\\\\"):
        # UNC 路径：\\\\server\\share → \\\\?\\UNC\\server\\share
        return Path(LONG_UNC_PREFIX + s.lstrip("\\"))
    return Path(LONG_PATH_PREFIX + s)


def get_persist_root() -> Path:
    """获取持久化根目录（不需管理员权限）.

    Returns:
        Windows: %APPDATA%\\FileMaster
        Mac: ~/Library/Application Support/FileMaster
        Linux: $XDG_DATA_HOME/filemaster 或 ~/.local/share/filemaster
    """
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "FileMaster"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FileMaster"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "filemaster"
    return Path.home() / ".local" / "share" / "filemaster"
