"""分类引擎.

W3 详细实现：
- 内置 5 类（PDF / WORD / EXCEL / PPT / IMAGE）
- 自定义规则（扩展名 / MIME / 正则 / 大小 / 时间）
- 规则优先级
- 可视化规则编辑（rules.json）
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# 内置分类
BUILTIN_CATEGORIES: dict[str, tuple[str, ...]] = {
    "PDF": (".pdf",),
    "WORD": (".doc", ".docx", ".rtf", ".odt"),
    "EXCEL": (".xls", ".xlsx", ".csv", ".ods"),
    "PPT": (".ppt", ".pptx", ".odp"),
    "IMAGE": (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg"),
}


@dataclass(frozen=True)
class ClassificationRule:
    """单条分类规则."""

    category: str
    extensions: tuple[str, ...] = ()
    pattern: str | None = None  # 正则匹配文件名
    min_size: int | None = None  # bytes
    max_size: int | None = None  # bytes
    enabled: bool = True


@dataclass
class ClassificationResult:
    """分类结果."""

    source: Path
    target_dir: Path
    category: str
    matched_rule: str | None = None


@dataclass
class Classifier:
    """分类器.

    W3 详细实现：规则合并 / 优先级 / 文件系统操作。
    """

    rules: list[ClassificationRule] = field(default_factory=list)
    target_root: Path | None = None

    @classmethod
    def from_builtin(cls) -> "Classifier":
        """用内置 5 类构造分类器."""
        rules = [
            ClassificationRule(category=cat, extensions=exts)
            for cat, exts in BUILTIN_CATEGORIES.items()
        ]
        return cls(rules=rules)

    def classify(self, file: Path) -> ClassificationResult | None:
        """对单个文件分类.

        Args:
            file: 源文件
        Returns:
            分类结果；若没有规则匹配返回 None
        """
        for rule in self.rules:
            if not rule.enabled:
                continue
            if rule.extensions and file.suffix.lower() not in rule.extensions:
                continue
            if rule.pattern and not re.search(rule.pattern, file.name):
                continue
            if rule.min_size is not None and file.stat().st_size < rule.min_size:
                continue
            if rule.max_size is not None and file.stat().st_size > rule.max_size:
                continue
            assert self.target_root is not None
            return ClassificationResult(
                source=file,
                target_dir=self.target_root / rule.category,
                category=rule.category,
                matched_rule=rule.category,
            )
        return None

    def classify_all(self, files: Iterable[Path]) -> list[ClassificationResult]:
        """批量分类."""
        return [r for r in (self.classify(f) for f in files) if r is not None]
