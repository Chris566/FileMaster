"""模板引擎测试.

覆盖：
- 基本字面量
- 占位符替换
- 数字格式化（Index:D3 和 Index:0NN 两种）
- 多占位符
- 边界：空模板、未知占位符
- 不变性（frozen）
- 哈希相等性
- W2: Index:0NN 写法、Hash/Date/Sheet 等占位符
"""

from __future__ import annotations

import pytest

from filemaster.core.template import Template


class TestTemplateBasics:
    """基础渲染."""

    def test_literal_only(self) -> None:
        t = Template("plain_string")
        assert t.render({}) == "plain_string"

    def test_single_placeholder(self) -> None:
        t = Template("{Prefix}")
        assert t.render({"Prefix": "X_"}) == "X_"

    def test_multiple_placeholders(self) -> None:
        t = Template("{Prefix}_{Index}_{BaseName}.{Extension}")
        ctx = {"Prefix": "P", "Index": 1, "BaseName": "doc", "Extension": "pdf"}
        assert t.render(ctx) == "P_1_doc.pdf"

    def test_empty_string_context_value(self) -> None:
        t = Template("{Prefix}{Name}")
        assert t.render({"Prefix": "", "Name": "x"}) == "x"


class TestTemplateFormatting:
    """格式化."""

    def test_index_zero_padded_3(self) -> None:
        t = Template("{Index:D3}")
        assert t.render({"Index": 5}) == "005"
        assert t.render({"Index": 100}) == "100"

    def test_index_zero_padded_4(self) -> None:
        t = Template("{Index:D4}")
        assert t.render({"Index": 7}) == "0007"

    def test_index_zero_padded_legacy_syntax(self) -> None:
        """W2: 支持 Index:03 写法（与 D3 等价）."""
        t = Template("{Index:03}")
        assert t.render({"Index": 5}) == "005"
        assert t.render({"Index": 100}) == "100"

    def test_index_zero_padded_legacy_5(self) -> None:
        t = Template("{Index:05}")
        assert t.render({"Index": 42}) == "00042"

    def test_unknown_formatter_keeps_value(self) -> None:
        t = Template("{Index:Foo}")
        assert t.render({"Index": 5}) == "5"


class TestTemplateValidation:
    """校验."""

    def test_empty_template_raises(self) -> None:
        with pytest.raises(ValueError, match="模板不能为空"):
            Template("")

    def test_placeholders_list(self) -> None:
        t = Template("{A}_{B}_{A}")
        placeholders = t.placeholders()
        assert len(placeholders) == 3
        assert placeholders[0].name == "A"
        assert placeholders[1].name == "B"
        assert placeholders[2].name == "A"
        assert placeholders[0].formatter is None
        assert placeholders[1].formatter is None

    def test_placeholders_with_formatter(self) -> None:
        t = Template("{Index:D3}")
        ph = t.placeholders()
        assert ph[0].name == "Index"
        assert ph[0].formatter == "D3"

    def test_unknown_placeholder_kept_literal(self) -> None:
        t = Template("{Unknown}")
        assert t.render({}) == ""


class TestTemplateImmutability:
    """不可变."""

    def test_frozen(self) -> None:
        t = Template("X")
        with pytest.raises(Exception):  # FrozenInstanceError
            t.raw = "Y"  # type: ignore[misc]

    def test_equality(self) -> None:
        assert Template("X") == Template("X")
        assert Template("X") != Template("Y")

    def test_hash(self) -> None:
        s = {Template("A"), Template("A"), Template("B")}
        assert len(s) == 2

    def test_repr(self) -> None:
        assert repr(Template("X")) == "Template('X')"
        assert repr(Template("X_{Y}")) == "Template('X_{Y}')"


class TestTemplateReprAndStr:
    def test_str(self) -> None:
        assert str(Template("X")) == "X"

    def test_raw_property(self) -> None:
        t = Template("X")
        assert t.raw == "X"


# ============ W2 新增测试 ============


class TestW2Placeholders:
    """W2 新增占位符渲染."""

    def test_filesize(self) -> None:
        t = Template("{FileSize}_{OriginalName}")
        assert t.render({"FileSize": "1.5 MB", "OriginalName": "a.pdf"}) == "1.5 MB_a.pdf"

    def test_filesize_bytes(self) -> None:
        t = Template("doc_{FileSizeBytes}.txt")
        assert t.render({"FileSizeBytes": 1234}) == "doc_1234.txt"

    def test_created_date(self) -> None:
        t = Template("{CreatedDate}_{OriginalName}")
        assert (
            t.render({"CreatedDate": "2026-08-30", "OriginalName": "a.pdf"})
            == "2026-08-30_a.pdf"
        )

    def test_modified_date(self) -> None:
        t = Template("{ModifiedDate}_{OriginalName}")
        assert (
            t.render({"ModifiedDate": "2026-07-15", "OriginalName": "a.pdf"})
            == "2026-07-15_a.pdf"
        )

    def test_hash_short(self) -> None:
        t = Template("{HashShort}_{OriginalName}")
        assert (
            t.render({"HashShort": "5eb63bbb", "OriginalName": "a.pdf"})
            == "5eb63bbb_a.pdf"
        )

    def test_hash_full(self) -> None:
        t = Template("{Hash}_{OriginalName}")
        assert (
            t.render({"Hash": "5eb63bbbe01eeed093cb22bb8f5acdc3", "OriginalName": "a.pdf"})
            == "5eb63bbbe01eeed093cb22bb8f5acdc3_a.pdf"
        )

    def test_sheet_placeholder(self) -> None:
        t = Template("{Sheet}_{OriginalName}")
        assert t.render({"Sheet": "销售数据", "OriginalName": "a.xlsx"}) == "销售数据_a.xlsx"

    def test_all_w2_placeholders_together(self) -> None:
        """综合: 真实 W2 模板."""
        t = Template("{CreatedDate}_{FileSize}_{Index:D3}_{OriginalName}")
        ctx = {
            "CreatedDate": "2026-08-30",
            "FileSize": "1.2 MB",
            "Index": 5,
            "OriginalName": "report.pdf",
        }
        assert t.render(ctx) == "2026-08-30_1.2 MB_005_report.pdf"
