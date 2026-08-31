"""业务核心.

所有 UI 无关的业务逻辑都在 core/ 下，方便单测。
W2-W4 会在此填实现细节。
"""

from filemaster.core.classifier import Classifier
from filemaster.core.preview import (
    FileMetadata,
    PreviewContent,
    PreviewGenerator,
    PreviewKind,
    build_preview,
)
from filemaster.core.renamer import Renamer
from filemaster.core.template import Template
from filemaster.core.undo import UndoEntry, UndoStack

__all__ = [
    "Renamer",
    "Template",
    "Classifier",
    "PreviewGenerator",
    "PreviewKind",
    "PreviewContent",
    "FileMetadata",
    "build_preview",
    "UndoStack",
    "UndoEntry",
]
