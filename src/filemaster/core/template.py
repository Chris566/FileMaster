"""占位符模板引擎.

支持的占位符（按域分类）：

**基础（W1）**
- {Prefix}           前缀
- {OriginalName}    原始文件名（含扩展名）
- {BaseName}        原始文件名（不含扩展名）
- {Extension}       扩展名（不含点号）
- {Index}           序号（默认 1 起）
- {Index:D3}        补零序号（如 001）；支持 D4/D5/D6
- {Date}            当前日期（YYYY-MM-DD）—— W2 由 renamer 注入
- {Time}            当前时间（HHmmss）—— W2 由 renamer 注入

**文件属性（W2 新增）**
- {FileSize}        人类可读大小，如 "1.5 MB"
- {FileSizeBytes}   字节数
- {CreatedDate}     文件创建日期（YYYY-MM-DD）
- {ModifiedDate}    文件修改日期（YYYY-MM-DD）
- {HashShort}       文件 md5 前 8 位
- {Sheet}           Excel 第一个 sheet 名（非 Excel 留空）

**文档元数据（W3 新增，由 MetadataReader 注入 context）**
- {Title}           PDF/Word/Excel 文档标题（无则空串）
- {Author}          文档作者（PDF/Word 直接读；Excel 取 creator；Image 取 EXIF Artist）
- {Subject}         PDF/Word/Excel 主题/描述
- {PageCount}       PDF/Word 段落数（Word 简化版 = paragraph 数）
- {ImageWidth}      图片宽度（像素，非图片返 0）
- {ImageHeight}     图片高度（像素，非图片返 0）

**预留（W9+）**
- {Regex:pat→repl}  正则替换
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

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
                # Index 的补零格式化
                if token.formatter and token.name == "Index":
                    try:
                        spec = token.formatter
                        # 支持 D3/D4/... 与 03/04/... 两种写法
                        if spec.upper().startswith("D"):
                            width = int(spec[1:])
                        else:
                            width = int(spec.lstrip("0") or "0")
                        value = f"{int(value):0{width}d}"
                    except (ValueError, TypeError):
                        pass
                parts.append(str(value))
            else:
                parts.append(token)
        return "".join(parts)

    def placeholders(self) -> list[PlaceholderSpec]:
        """列出模板里所有占位符（顺序保留）."""
        return [t for t in self._tokens if isinstance(t, PlaceholderSpec)]
