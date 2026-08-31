"""W4 v2: 异步文件预览 Worker (QObject + QThread).

跟 W4 v1 ClassifyWorker 一样模式: Worker 在子线程跑, 通过信号回主线程.
W8 升级: 跟 dedup.py / batch.py 一样, 用 CancellationToken 替代 _cancelled bool.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from filemaster.core.cancellation import CancellationToken
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
        cancelled(): W8: 用户切到下一行, 当前预览放弃 (无参数, 单文件没"已处理 N 个"概念)
        finished(): 总是最后 emit 一次, 用来清理 QThread
    """

    succeeded = Signal(object, object)  # FileMetadata, PreviewContent
    failed = Signal(str, str)           # path_str, error
    cancelled = Signal()                # W8: 单文件取消, 无参数
    finished = Signal()

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        # W8: 协作式取消令牌, 跟 batch / dedup worker 保持一致
        self._token = CancellationToken()
        self._gen = PreviewGenerator()

    def cancel(self) -> None:
        """请求取消. 实际仍在当前文件 IO 完成才退出 (小文件 < 100ms)."""
        self._token.cancel()

    @property
    def cancellation_token(self) -> CancellationToken:
        """暴露 token 供外部 (测试 / 状态查询) 使用."""
        return self._token

    def run(self) -> None:
        """同步跑 (在子线程). 大文件会被 build_preview 限速,通常 < 200ms."""
        try:
            if self._token.is_cancelled:
                self.cancelled.emit()
                return
            # 故意不 sleep — 用户期待立即看到结果
            # 但加个 5ms 让主线程有机会刷新
            time.sleep(0.005)
            if self._token.is_cancelled:
                self.cancelled.emit()
                return
            meta, content = self._gen.generate(self._path)
            if self._token.is_cancelled:
                self.cancelled.emit()
                return
            self.succeeded.emit(meta, content)
        except Exception as e:
            self.failed.emit(str(self._path), str(e))
        finally:
            self.finished.emit()
