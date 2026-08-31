"""分类引擎测试."""

from __future__ import annotations

import time
from pathlib import Path

from filemaster.core.classifier import (
    BUILTIN_CATEGORIES,
    EXTENSION_MAP,
    MAGIC_SIGNATURES,
    Category,
    Classification,
    ClassificationResult,
    ClassificationRule,
    Classifier,
    DetectionMethod,
    classify_batch,
    classify_file,
    group_by_category,
)

# ============================================================
# W3 旧测试（保留，向后兼容）
# ============================================================


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


# ============================================================
# W4 v1 新测试
# ============================================================


# ---------- Fixture 构造函数 ----------


def _make_pdf(p: Path, title: str = "test") -> Path:
    """写一个最小 PDF（含 %PDF- magic）。"""
    f = p / f"{title}.pdf"
    f.write_bytes(b"%PDF-1.4\n%test\n")
    return f


def _make_png(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.png"
    # 完整 PNG header (8 bytes) + minimal IHDR
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 100)
    return f


def _make_jpeg(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    return f


def _make_zip(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.zip"
    f.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    return f


def _make_mp4(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.mp4"
    # MP4: 4 bytes box size + "ftyp" at offset 4
    f.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 100)
    return f


def _make_ogg(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.ogg"
    f.write_bytes(b"OggS" + b"\x00" * 100)
    return f


def _make_elf(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.elf"
    f.write_bytes(b"\x7fELF" + b"\x00" * 100)
    return f


class TestCategoryEnum:
    """Category 枚举与中文标签."""

    def test_all_12_categories(self) -> None:
        assert len(Category) == 12

    def test_label_zh_unique(self) -> None:
        labels = [c.label_zh for c in Category]
        assert len(labels) == len(set(labels)), "label_zh 必须唯一"

    def test_label_zh_not_empty(self) -> None:
        for c in Category:
            assert c.label_zh, f"{c.value} 缺少中文标签"

    def test_str_serialization(self) -> None:
        # 枚举的 str 值必须和 .value 一致（便于 JSON 序列化）
        for c in Category:
            assert c == c.value
            assert c.value in {"PDF", "DOCUMENT", "IMAGE", "VIDEO", "AUDIO",
                               "ARCHIVE", "CODE", "CONFIG", "OTHER", "UNKNOWN",
                               "SPREADSHEET", "PRESENTATION"}


class TestMagicBytes:
    """Magic bytes 检测（不依赖真实文件类型，造 minimal 头即可）."""

    def test_pdf_magic(self, tmp_path: Path) -> None:
        f = _make_pdf(tmp_path)
        c = classify_file(f)
        assert c.category == Category.PDF
        assert c.method == DetectionMethod.MAGIC
        assert c.confidence == 0.95
        assert c.mime_type == "application/pdf"

    def test_png_magic(self, tmp_path: Path) -> None:
        f = _make_png(tmp_path)
        c = classify_file(f)
        assert c.category == Category.IMAGE
        assert c.method == DetectionMethod.MAGIC
        assert c.mime_type == "image/png"

    def test_jpeg_magic(self, tmp_path: Path) -> None:
        f = _make_jpeg(tmp_path)
        c = classify_file(f)
        assert c.category == Category.IMAGE
        assert c.mime_type == "image/jpeg"

    def test_zip_magic(self, tmp_path: Path) -> None:
        f = _make_zip(tmp_path)
        c = classify_file(f)
        # .zip 扩展名 → magic 也命中 ZIP → 归 ARCHIVE
        assert c.category == Category.ARCHIVE
        assert c.method == DetectionMethod.MAGIC

    def test_mp4_magic(self, tmp_path: Path) -> None:
        f = _make_mp4(tmp_path)
        c = classify_file(f)
        assert c.category == Category.VIDEO
        assert c.mime_type == "video/mp4"

    def test_ogg_magic(self, tmp_path: Path) -> None:
        f = _make_ogg(tmp_path)
        c = classify_file(f)
        assert c.category == Category.AUDIO
        assert c.mime_type == "audio/ogg"

    def test_elf_magic(self, tmp_path: Path) -> None:
        f = _make_elf(tmp_path)
        c = classify_file(f)
        assert c.category == Category.CODE
        assert c.mime_type == "application/x-executable"


class TestOfficeContainerOverride:
    """Office 文件（docx/xlsx/pptx）是 ZIP 容器，扩展名应升级到对应分类."""

    def test_docx_is_document_not_archive(self, tmp_path: Path) -> None:
        f = tmp_path / "report.docx"
        f.write_bytes(b"PK\x03\x04" + b"\x00" * 100)  # ZIP magic
        c = classify_file(f)
        assert c.category == Category.DOCUMENT, f"expected DOCUMENT, got {c.category}"

    def test_xlsx_is_spreadsheet_not_archive(self, tmp_path: Path) -> None:
        f = tmp_path / "data.xlsx"
        f.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
        c = classify_file(f)
        assert c.category == Category.SPREADSHEET

    def test_pptx_is_presentation_not_archive(self, tmp_path: Path) -> None:
        f = tmp_path / "slides.pptx"
        f.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
        c = classify_file(f)
        assert c.category == Category.PRESENTATION

    def test_odt_is_document(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.odt"
        f.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
        c = classify_file(f)
        assert c.category == Category.DOCUMENT


class TestExtensionFallback:
    """Magic 没命中时扩展名兜底."""

    def test_python_file_by_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "script.py"
        f.write_text("print('hi')")
        c = classify_file(f)
        assert c.category == Category.CODE
        assert c.method == DetectionMethod.EXTENSION
        assert 0.7 <= c.confidence <= 0.8

    def test_json_config(self, tmp_path: Path) -> None:
        f = tmp_path / "config.json"
        f.write_text('{"k": "v"}')
        c = classify_file(f)
        assert c.category == Category.CONFIG

    def test_text_file(self, tmp_path: Path) -> None:
        f = tmp_path / "notes.txt"
        f.write_text("hello world")
        c = classify_file(f)
        assert c.category == Category.DOCUMENT

    def test_unknown_extension_returns_other(self, tmp_path: Path) -> None:
        f = tmp_path / "data.xyz"
        f.write_bytes(b"\x00" * 100)  # 无 magic
        c = classify_file(f)
        # 既不命中 magic，也不命中扩展名表
        assert c.category in (Category.OTHER, Category.UNKNOWN)
        assert c.confidence < 0.5

    def test_no_extension_returns_other(self, tmp_path: Path) -> None:
        f = tmp_path / "README"
        f.write_text("readme")
        c = classify_file(f)
        assert c.category in (Category.OTHER, Category.UNKNOWN)


class TestConfidenceLevels:
    """Confidence 三档：MAGIC(0.95) > EXTENSION(0.75) > FALLBACK(0.0-0.4)."""

    def test_magic_highest_confidence(self, tmp_path: Path) -> None:
        f = _make_pdf(tmp_path)
        c = classify_file(f)
        assert c.confidence >= 0.9

    def test_extension_medium_confidence(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        c = classify_file(f)
        assert 0.6 <= c.confidence <= 0.85

    def test_unknown_lowest_confidence(self, tmp_path: Path) -> None:
        f = tmp_path / "a.xyzunknown"
        f.write_bytes(b"\x00" * 10)
        c = classify_file(f)
        assert c.confidence <= 0.5


class TestBoundary:
    """边界条件."""

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        f = tmp_path / "ghost.pdf"  # 不创建
        c = classify_file(f)
        # 扩展名命中 → DOCUMENT/PDF 走 EXTENSION 路径
        # 因为没创建文件，magic 读取会失败但扩展名兜底
        assert c.category in (Category.PDF, Category.UNKNOWN, Category.OTHER)

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        c = classify_file(f)
        # magic 不会命中（0 字节），扩展名兜底为 DOCUMENT
        assert c.category == Category.DOCUMENT
        assert c.method == DetectionMethod.EXTENSION

    def test_corrupted_pdf_extension_fallback(self, tmp_path: Path) -> None:
        f = tmp_path / "broken.pdf"
        f.write_bytes(b"this is not a pdf")
        c = classify_file(f)
        # magic 不会命中，扩展名兜底
        # 但我们期望 confidence 较低因为扩展名显示是 PDF
        assert c.method in (DetectionMethod.EXTENSION, DetectionMethod.MAGIC)

    def test_classify_returns_dataclass(self, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("x")
        c = classify_file(f)
        assert isinstance(c, Classification)
        assert c.source == f
        assert isinstance(c.category, Category)
        assert 0.0 <= c.confidence <= 1.0


class TestClassifyBatch:
    """批量分类."""

    def test_batch_preserves_order(self, tmp_path: Path) -> None:
        files = [
            _make_pdf(tmp_path, "a"),
            _make_png(tmp_path, "b"),
            _make_zip(tmp_path, "c"),
        ]
        results = classify_batch(files)
        assert len(results) == 3
        assert results[0].category == Category.PDF
        assert results[1].category == Category.IMAGE
        assert results[2].category == Category.ARCHIVE

    def test_batch_with_missing_file(self, tmp_path: Path) -> None:
        existing = _make_pdf(tmp_path, "exists")
        missing = tmp_path / "ghost.pdf"
        results = classify_batch([existing, missing])
        assert len(results) == 2
        # 缺失文件不应该崩


class TestGroupByCategory:
    """按 Category 分组."""

    def test_group_distributes(self, tmp_path: Path) -> None:
        files = [
            _make_pdf(tmp_path, "a"),
            _make_pdf(tmp_path, "b"),
            _make_png(tmp_path, "c"),
        ]
        classifications = classify_batch(files)
        groups = group_by_category(classifications)
        assert Category.PDF in groups
        assert Category.IMAGE in groups
        assert len(groups[Category.PDF]) == 2
        assert len(groups[Category.IMAGE]) == 1

    def test_group_excludes_empty_categories(self, tmp_path: Path) -> None:
        files = [_make_pdf(tmp_path, "a")]
        classifications = classify_batch(files)
        groups = group_by_category(classifications)
        # 不会包含空 category
        assert Category.VIDEO not in groups
        assert Category.AUDIO not in groups


class TestPerformance:
    """性能：1000 文件 < 2s."""

    def test_1000_files_under_2s(self, tmp_path: Path) -> None:
        files = []
        for i in range(1000):
            ext_idx = i % 5
            if ext_idx == 0:
                files.append(_make_pdf(tmp_path, f"f{i}"))
            elif ext_idx == 1:
                files.append(_make_png(tmp_path, f"f{i}"))
            elif ext_idx == 2:
                files.append(_make_zip(tmp_path, f"f{i}"))
            elif ext_idx == 3:
                files.append(_make_mp4(tmp_path, f"f{i}"))
            else:
                # 文本文件
                f = tmp_path / f"f{i}.txt"
                f.write_text(f"text {i}")
                files.append(f)

        start = time.perf_counter()
        results = classify_batch(files)
        elapsed = time.perf_counter() - start

        assert len(results) == 1000
        assert elapsed < 2.0, f"1000 文件分类耗时 {elapsed:.2f}s 超过 2s 阈值"


class TestExtensionMap:
    """EXTENSION_MAP 完整性."""

    def test_extension_map_covers_major_types(self) -> None:
        # 11 大类每类至少有 1 个扩展名
        categories_in_map = set(EXTENSION_MAP.values())
        required = {
            Category.PDF, Category.DOCUMENT, Category.SPREADSHEET,
            Category.PRESENTATION, Category.IMAGE, Category.VIDEO,
            Category.AUDIO, Category.ARCHIVE, Category.CODE, Category.CONFIG,
        }
        missing = required - categories_in_map
        assert not missing, f"EXTENSION_MAP 缺少类别: {missing}"

    def test_extension_map_keys_lowercase(self) -> None:
        for k in EXTENSION_MAP:
            assert k == k.lower(), f"扩展名键必须小写: {k}"


class TestDetectionMethod:
    """DetectionMethod 枚举."""

    def test_four_methods(self) -> None:
        assert len(DetectionMethod) == 4
        methods = {m.value for m in DetectionMethod}
        assert methods == {"magic", "extension", "ext_only", "fallback"}


class TestMagicSignatures:
    """MAGIC_SIGNATURES 完整性."""

    def test_at_least_20_signatures(self) -> None:
        # 至少覆盖 PDF + 5 image + 3 archive + 3 video + 2 audio + 3 code = 16
        # 实际应 > 20
        assert len(MAGIC_SIGNATURES) >= 20, (
            f"只定义了 {len(MAGIC_SIGNATURES)} 个 magic 签名，"
            "W4 v1 应至少覆盖 20+ 类型"
        )

    def test_all_signatures_have_valid_category(self) -> None:
        for _offset, _sig, cat, _mime in MAGIC_SIGNATURES:
            assert isinstance(cat, Category)
            assert cat != Category.OTHER, "magic 签名不应落到 OTHER"


class TestLegacyAPI:
    """旧 Classifier API（W3 test 兼容 + 健壮性）."""

    def test_legacy_classifier_with_custom_rule(self, tmp_path: Path) -> None:
        cls = Classifier(rules=[
            ClassificationRule(category="CUSTOM", pattern=r"^X_", extensions=(".dat",))
        ], target_root=tmp_path)
        f = tmp_path / "X_data.dat"
        f.write_bytes(b"x")
        result = cls.classify(f)
        assert result is not None
        assert isinstance(result, ClassificationResult)
        assert result.category == "CUSTOM"

    def test_builtin_categories_keys(self) -> None:
        assert set(BUILTIN_CATEGORIES.keys()) == {"PDF", "WORD", "EXCEL", "PPT", "IMAGE"}
