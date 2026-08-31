"""W4 v2: 异步文件预览 Worker (QObject + QThread).

跟 W4 v1 ClassifyWorker 一样模式: Worker 在子线程跑, 通过信号回主线程.
Worker 自带 cancellation: cancel() 后下一次循环检查并退出.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from filemaster.core.preview import (
    FileMetadata,
    PreviewContent,
    PreviewGenerator,
)


class PreviewWorker(QObject):
    """单文件预览 Worker — 选行时启动, 完成后 emit 一次结果.

    Signals:
        succeeded(meta, content): 成功
        failed(path, error): 失败 (极少, build_preview 内部已 try/except 兜底)
        finished(): 总是最后 emit 一次, 用来清理 QThread
    """

    succeeded = Signal(object, object)  # FileMetadata, PreviewContent
    failed = Signal(str, str)           # path_str, error
    finished = Signal()

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._cancelled = False
        self._gen = PreviewGenerator()

    def cancel(self) -> None:
        """请求取消. 实际仍在当前文件 IO 完成才退出 (小文件 < 100ms)."""
        self._cancelled = True

    def run(self) -> None:
        """同步跑 (在子线程). 大文件会被 build_preview 限速,通常 < 200ms."""
        try:
            if self._cancelled:
                self.finished.emit()
                return
            # 故意不 sleep — 用户期待立即看到结果
            # 但加个 5ms 让主线程有机会刷新
            time.sleep(0.005)
            if self._cancelled:
                self.finished.emit()
                return
            meta, content = self._gen.generate(self._path)
            if self._cancelled:
                self.finished.emit()
                return
            self.succeeded.emit(meta, content)
        except Exception as e:
            self.failed.emit(str(self._path), str(e))
        finally:
            self.finished.emit()
