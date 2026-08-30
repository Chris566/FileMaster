"""业务核心.

所有 UI 无关的业务逻辑都在 core/ 下，方便单测。
W2-W4 会在此填实现细节。
"""

from filemaster.core.classifier import Classifier
from filemaster.core.preview import PreviewGenerator
from filemaster.core.renamer import Renamer
from filemaster.core.template import Template
from filemaster.core.undo import UndoEntry, UndoStack

__all__ = [
    "Renamer",
    "Template",
    "Classifier",
    "PreviewGenerator",
    "UndoStack",
    "UndoEntry",
]
