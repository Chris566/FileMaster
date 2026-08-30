"""文件去重（W10 详细实现）.

按 MD5 / SHA1 / SHA256 哈希分组，重复文件移到 _duplicates/ 目录。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from filemaster.utils.hash import file_hash


@dataclass(frozen=True)
class DuplicateGroup:
    """一组重复文件."""

    hash_value: str
    files: tuple[Path, ...]

    @property
    def keeper(self) -> Path:
        """保留的（最早修改时间的）."""
        return min(self.files, key=lambda f: f.stat().st_mtime)


@dataclass
class Deduper:
    """去重器."""

    algorithm: str = "md5"  # md5 | sha1 | sha256
    duplicate_dir_name: str = "_duplicates"

    def find_duplicates(self, files: Iterable[Path]) -> list[DuplicateGroup]:
        """找出所有重复组.

        Args:
            files: 文件列表
        Returns:
            重复组列表（每组至少 2 个文件）
        """
        hash_to_files: dict[str, list[Path]] = defaultdict(list)
        for f in files:
            if not f.is_file():
                continue
            h = file_hash(f, self.algorithm)
            hash_to_files[h].append(f)
        groups = [DuplicateGroup(h, tuple(fs)) for h, fs in hash_to_files.items() if len(fs) > 1]
        # 优先按文件数排序，方便用户看
        groups.sort(key=lambda g: -len(g.files))
        return groups

    def get_stats(self, files: Iterable[Path]) -> dict[str, int]:
        """统计去重前后空间.

        Returns:
            {"total_files": N, "duplicates": M, "wasted_bytes": B, "savings_bytes": B}
        """
        groups = self.find_duplicates(files)
        total_files = sum(1 for _ in files)
        duplicates = sum(len(g.files) - 1 for g in groups)
        wasted_bytes = 0
        for g in groups:
            size = g.files[0].stat().st_size
            wasted_bytes += size * (len(g.files) - 1)
        return {
            "total_files": total_files,
            "duplicates": duplicates,
            "wasted_bytes": wasted_bytes,
            "savings_bytes": wasted_bytes,
        }
