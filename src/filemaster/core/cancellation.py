"""协作式取消令牌 (W7).

设计要点:
- 状态对象 (is_cancelled), 不绑线程/事件循环
- 调用方负责 cancel() 的线程安全 (主线程调用, worker 线程读)
- 引擎在每个文件之间检查 is_cancelled, 不打断单文件处理
- 适用场景: GUI 取消按钮、CLI Esc 键、批量任务中止
"""

from __future__ import annotations


class CancellationToken:
    """协作式取消令牌.

    典型用法:
        token = CancellationToken()
        # 主线程: 某事件触发 cancel
        token.cancel()
        # worker 线程: 每文件处理前查
        for file in files:
            if token.is_cancelled:
                break
            process(file)
    """

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消 (幂等)."""
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        """是否已请求取消. 检查在每文件之间, 协作式."""
        return self._cancelled

    def reset(self) -> None:
        """重置 (供 worker 复用, 罕见)."""
        self._cancelled = False
