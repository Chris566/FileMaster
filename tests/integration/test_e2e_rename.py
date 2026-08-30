"""FileMaster 集成测试."""

from __future__ import annotations

from pathlib import Path

import pytest

from filemaster.core.renamer import Renamer
from filemaster.core.template import Template


@pytest.mark.integration
class TestRenameEndToEnd:
    """端到端：模板 → 计划 → 验证."""

    def test_full_rename_pipeline(self, sample_files: list[Path]) -> None:
        # 1. 构造模板
        tpl = Template("{Prefix}_{Index:D3}_{OriginalName}")

        # 2. 计划
        renamer = Renamer(tpl, prefix="X")
        results = renamer.plan(sample_files)

        # 3. 验证
        assert len(results) == 3
        assert results[0].target.name.startswith("X_001_")
        assert results[2].target.name.startswith("X_003_")

    def test_rename_then_classify(self, sample_files: list[Path]) -> None:
        from filemaster.core.classifier import Classifier

        tpl = Template("{Prefix}{OriginalName}")
        renamer = Renamer(tpl, prefix="NEW_")
        results = renamer.plan(sample_files)

        # 模拟重命名后分类
        new_files = [r.target for r in results if r.target]
        cls = Classifier.from_builtin()
        cls.target_root = sample_files[0].parent / "classified"
        classified = cls.classify_all(new_files)

        assert len(classified) == 3
        assert all(c.category == "PDF" for c in classified)


@pytest.mark.integration
class TestHashIntegration:
    """哈希集成."""

    def test_dedup_with_real_files(self, tmp_path: Path) -> None:
        from filemaster.core.dedup import Deduper

        # 创建 3 个文件，2 重复
        (tmp_path / "a.pdf").write_bytes(b"same content")
        (tmp_path / "b.pdf").write_bytes(b"same content")
        (tmp_path / "c.pdf").write_bytes(b"different content")

        dedup = Deduper(algorithm="md5")
        groups = dedup.find_duplicates(list(tmp_path.glob("*.pdf")))

        assert len(groups) == 1
        assert len(groups[0].files) == 2
