"""Qt 跨线程信号定义."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class ProgressSignal(QObject):
    """进度信号（Worker → UI）."""

    # percent, current_file, total, message
    progressed = Signal(int, str, int, str)

    # 阶段：scan / rename / classify / done
    stage_changed = Signal(str, str)

    # 整体开始/结束
    started = Signal(int)  # total
    finished = Signal(dict)  # stats


class ErrorSignal(QObject):
    """错误信号."""

    # file, error_message
    file_failed = Signal(str, str)

    # 严重错误
    fatal = Signal(str)
