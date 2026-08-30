"""配置管理（W4 详细实现）.

写到 %APPDATA%\\FileMaster\\config.json（Windows）。
Linux/Mac: ~/.config/filemaster/config.json。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_config_dir() -> Path:
    """获取配置目录（不需要管理员权限）."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "FileMaster"
    # macOS / Linux
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "filemaster"
    return Path.home() / ".config" / "filemaster"


@dataclass
class Config:
    """配置对象."""

    version: str = "0.1.0"
    theme: str = "light"  # light | dark | fluent | high_contrast
    language: str = "zh_CN"
    last_source_dir: str = ""
    last_target_dir: str = ""
    last_prefix: str = ""
    last_template: str = "{Prefix}{OriginalName}"
    classify_enabled: bool = True
    dry_run: bool = False
    overwrite_mode: str = "ask"  # ask | overwrite_all | skip_all
    undo_keep_count: int = 50
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """从文件加载.

        Args:
            path: 配置文件路径，None 用默认
        Returns:
            Config 对象（文件不存在时返回默认）
        """
        if path is None:
            path = default_config_dir() / "config.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return cls()

    def save(self, path: Path | None = None) -> None:
        """保存到文件.

        Args:
            path: 配置文件路径，None 用默认
        """
        if path is None:
            path = default_config_dir() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def touch_history(self) -> Path:
        """保存当前配置到历史快照.

        Returns:
            快照文件路径
        """
        hist_dir = default_config_dir() / "ChangeHistory"
        hist_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot = hist_dir / f"config_{stamp}.json"
        self.save(snapshot)
        return snapshot
