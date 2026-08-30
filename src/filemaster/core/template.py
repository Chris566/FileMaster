"""占位符模板引擎.

支持的占位符（持续扩展，W2 详细文档化）：
- {Prefix}           前缀
- {OriginalName}    原始文件名（含扩展名）
- {BaseName}        原始文件名（不含扩展名）
- {Extension}       扩展名（不含点号）
- {Index}           序号（默认 1 起）
- {Index:D3}        补零序号（如 001）
- {Date}            当前日期（YYYY-MM-DD）
- {Time}            当前时间（HHmmss）
- {Title}           PDF/Office 文档标题（W8 落地）
- {Author}          文档作者（W8 落地）
- {Regex:pat→repl}  正则替换（W9 落地）

W2 详细实现：parse / render / validate / 占位符索引文档化。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

# 简单占位符正则：{Name} 或 {Name:format}
_PLACEHOLDER_PATTERN = re.compile(r"\{(\w+)(?::([^}]+))?\}")


@dataclass(frozen=True)
class PlaceholderSpec:
    """占位符规格."""

    name: str
    formatter: str | None = None


class Template:
    """模板对象.

    不可变（frozen），可哈希，支持 ``.render(context)``。
    """

    __slots__ = ("_raw", "_tokens")

    def __init__(self, raw: str) -> None:
        """初始化.

        Args:
            raw: 模板字符串，如 ``"{Prefix}_{Index:D3}_{OriginalName}"``
        """
        if not raw:
            raise ValueError("模板不能为空")
        self._raw = raw
        self._tokens = self._tokenize(raw)

    @property
    def raw(self) -> str:
        """原始模板字符串."""
        return self._raw

    def __str__(self) -> str:
        return self._raw

    def __repr__(self) -> str:
        return f"Template({self._raw!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Template):
            return NotImplemented
        return self._raw == other._raw

    def __hash__(self) -> int:
        return hash(self._raw)

    @staticmethod
    def _tokenize(raw: str) -> list[PlaceholderSpec | str]:
        """把模板拆成 token 列表（占位符 + 字面量）."""
        tokens: list[PlaceholderSpec | str] = []
        last = 0
        for match in _PLACEHOLDER_PATTERN.finditer(raw):
            start, end = match.span()
            if start > last:
                tokens.append(raw[last:start])
            tokens.append(PlaceholderSpec(name=match.group(1), formatter=match.group(2)))
            last = end
        if last < len(raw):
            tokens.append(raw[last:])
        return tokens

    def render(self, context: Mapping[str, object]) -> str:
        """渲染模板.

        Args:
            context: 占位符名 -> 值
        Returns:
            渲染后的字符串
        """
        parts: list[str] = []
        for token in self._tokens:
            if isinstance(token, PlaceholderSpec):
                value = context.get(token.name, "")
                if token.formatter and token.name == "Index":
                    try:
                        width = int(token.formatter.lstrip("0Dd"))
                        value = f"{int(value):0{width}d}"
                    except ValueError:
                        pass
                parts.append(str(value))
            else:
                parts.append(token)
        return "".join(parts)

    def placeholders(self) -> list[PlaceholderSpec]:
        """列出模板里所有占位符（顺序保留）."""
        return [t for t in self._tokens if isinstance(t, PlaceholderSpec)]
