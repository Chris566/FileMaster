"""文件哈希工具.

支持 MD5 / SHA1 / SHA256 / BLAKE2b。
"""

from __future__ import annotations

import hashlib
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


def file_hash(file: Path, algorithm: str = "md5", chunk_size: int | None = None) -> str:
    """计算文件哈希.

    Args:
        file: 文件路径
        algorithm: 算法名（md5/sha1/sha256/blake2b）
        chunk_size: 块大小（默认自适应）
    Returns:
        十六进制摘要
    Raises:
        ValueError: 不支持的算法
        FileNotFoundError: 文件不存在
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
    return hasher.hexdigest()
