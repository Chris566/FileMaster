"""文件哈希工具.

支持 MD5 / SHA1 / SHA256 / BLAKE2b。
W9: 加 is_cancelled 支持, 大文件分块检查取消, 抛 InterruptedError.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

_ALGORITHMS = {
    "md5": hashlib.md5,
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "blake2b": hashlib.blake2b,
}

# 默认块大小：8KB（小文件）/ 1MB（大文件）
_CHUNK_SMALL = 8 * 1024
_CHUNK_LARGE = 1024 * 1024


class HashCancelledError(InterruptedError):
    """W9: hash 计算被取消.

    区别于 OSError / FileNotFoundError, 让 worker 单独处理取消场景
    (不写 undo entry, 不当作 ERROR, 当作"未完成").
    """
    def __init__(self, file: Path) -> None:
        super().__init__(f"hash cancelled: {file}")
        self.file = file


def file_hash(
    file: Path,
    algorithm: str = "md5",
    chunk_size: int | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    """计算文件哈希.

    Args:
        file: 文件路径
        algorithm: 算法名 (md5/sha1/sha256/blake2b)
        chunk_size: 块大小 (默认自适应)
        is_cancelled: W9 可选取消回调. 返回 True 时立即抛 HashCancelledError.
                      检查点在每块读取后, 不打断单块 I/O.
    Returns:
        十六进制摘要
    Raises:
        ValueError: 不支持的算法
        FileNotFoundError: 文件不存在
        HashCancelledError: is_cancelled 返回 True (W9)
    """
    algo = algorithm.lower()
    if algo not in _ALGORITHMS:
        raise ValueError(
            f"不支持的算法 {algorithm!r}，可选：{', '.join(_ALGORITHMS)}"
        )
    if not file.is_file():
        raise FileNotFoundError(file)

    hasher = _ALGORITHMS[algo]()
    if chunk_size is None:
        size = file.stat().st_size
        chunk_size = _CHUNK_LARGE if size > 100 * 1024 * 1024 else _CHUNK_SMALL

    with file.open("rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            hasher.update(block)
            # W9: 每块后检查取消 (大文件场景下, hash 计算可秒级)
            if is_cancelled is not None and is_cancelled():
                raise HashCancelledError(file)
    return hasher.hexdigest()
