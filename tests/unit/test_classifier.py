"""分类引擎测试."""

from __future__ import annotations

from pathlib import Path

from filemaster.core.classifier import (
    BUILTIN_CATEGORIES,
    ClassificationRule,
    Classifier,
)


class TestBuiltinClassifier:
    """内置 5 类分类器."""

    def test_pdf_classified_as_pdf(self, mixed_files: dict[str, list[Path]]) -> None:
        cls = Classifier.from_builtin()
        cls.target_root = mixed_files["PDF"][0].parent
        for f in mixed_files["PDF"]:
            result = cls.classify(f)
            assert result is not None
            assert result.category == "PDF"

    def test_png_classified_as_image(self, mixed_files: dict[str, list[Path]]) -> None:
        cls = Classifier.from_builtin()
        cls.target_root = mixed_files["IMAGE"][0].parent
        for f in mixed_files["IMAGE"]:
            result = cls.classify(f)
            assert result is not None
            assert result.category == "IMAGE"

    def test_unknown_extension_returns_none(self, tmp_path: Path) -> None:
        cls = Classifier.from_builtin()
        cls.target_root = tmp_path
        f = tmp_path / "weird.xyz"
        f.write_bytes(b"x")
        result = cls.classify(f)
        assert result is None


class TestCustomRules:
    """自定义规则."""

    def test_pattern_rule(self, tmp_path: Path) -> None:
        f1 = tmp_path / "INVOICE_001.pdf"
        f1.write_bytes(b"x")
        f2 = tmp_path / "report_001.pdf"
        f2.write_bytes(b"x")
        rules = [
            ClassificationRule(category="INVOICE", pattern=r"^INVOICE_", extensions=(".pdf",))
        ]
        cls = Classifier(rules=rules, target_root=tmp_path)
        assert cls.classify(f1).category == "INVOICE"
        assert cls.classify(f2) is None

    def test_disabled_rule_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "test.pdf"
        f.write_bytes(b"x")
        rules = [ClassificationRule(category="PDF", extensions=(".pdf",), enabled=False)]
        cls = Classifier(rules=rules, target_root=tmp_path)
        assert cls.classify(f) is None

    def test_size_filter(self, tmp_path: Path) -> None:
        small = tmp_path / "small.pdf"
        small.write_bytes(b"x" * 10)
        large = tmp_path / "large.pdf"
        large.write_bytes(b"x" * 1000)
        rules = [ClassificationRule(category="BIG_PDF", extensions=(".pdf",), min_size=500)]
        cls = Classifier(rules=rules, target_root=tmp_path)
        assert cls.classify(small) is None
        assert cls.classify(large).category == "BIG_PDF"


class TestClassifyAll:
    """批量分类."""

    def test_classify_all(self, mixed_files: dict[str, list[Path]]) -> None:
        cls = Classifier.from_builtin()
        cls.target_root = mixed_files["PDF"][0].parent
        all_files = [f for fs in mixed_files.values() for f in fs]
        results = cls.classify_all(all_files)
        # 12 个文件全部分类（4+3+2+2+1=12）
        assert len(results) == 12
