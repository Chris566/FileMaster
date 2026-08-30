"""配置管理测试."""

from __future__ import annotations

import json
from pathlib import Path

from filemaster.io.config import Config, default_config_dir


class TestConfig:
    """Config 加载/保存."""

    def test_default(self, tmp_path) -> None:
        cfg = Config.load(Path("/nonexistent/config.json"))
        assert cfg.version == "0.1.0"
        assert cfg.theme == "light"
        assert cfg.language == "zh_CN"

    def test_save_load_roundtrip(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        cfg = Config(
            theme="dark",
            last_prefix="X_",
            last_template="{Prefix}{OriginalName}",
            classify_enabled=False,
            dry_run=True,
        )
        cfg.save(path)
        assert path.exists()

        loaded = Config.load(path)
        assert loaded.theme == "dark"
        assert loaded.last_prefix == "X_"
        assert loaded.classify_enabled is False
        assert loaded.dry_run is True

    def test_creates_parent_dir(self, tmp_path) -> None:
        path = tmp_path / "nested" / "deeper" / "config.json"
        Config().save(path)
        assert path.exists()

    def test_corrupted_file_returns_default(self, tmp_path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not a valid json {{{", encoding="utf-8")
        cfg = Config.load(path)
        # 损坏时静默回退
        assert cfg.theme == "light"

    def test_extra_field_persists(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        cfg = Config()
        cfg.extra["custom_setting"] = 42
        cfg.save(path)
        loaded = Config.load(path)
        assert loaded.extra["custom_setting"] == 42


class TestDefaultConfigDir:
    """默认配置目录."""

    def test_returns_path(self) -> None:
        d = default_config_dir()
        assert isinstance(d, Path)
        assert d.name in ("FileMaster", "filemaster")  # Win / Unix
