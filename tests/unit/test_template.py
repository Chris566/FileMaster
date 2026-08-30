"""模板引擎测试.

覆盖：
- 基本字面量
- 占位符替换
- 数字格式化（Index:D3）
- 多占位符
- 边界：空模板、未知占位符
- 不变性（frozen）
- 哈希相等性
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
