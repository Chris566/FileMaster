"""文件去重（W4 v3：找重复 + metadata 集成）.

按 MD5 / SHA1 / SHA256 / BLAKE2B 哈希分组，重复文件展示在 GUI 表格里。
W4 v3 范围：**只查 + 表格预览**，不做移动/删除/硬链接（用户拍板）。
metadata 集成：每个 DuplicateGroup 带 hash_size + files_with_meta（path/size/mtime/ctime）。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from filemaster.utils.hash import file_hash


@dataclass(frozen=True)
class DuplicateFile:
    """单条重复文件的元信息（带文件系统元数据）.

    比 Path 多了 size/mtime/ctime, GUI 表格不用每次 stat.
    """

    path: Path
    size: int       # 字节
    mtime: float    # 修改时间 epoch
    ctime: float    # 创建时间 epoch


@dataclass(frozen=True)
class DuplicateGroup:
    """一组重复文件.

    Attributes:
        hash_value: 该组共享的 hash (md5/sha1/sha256/blake2b)
        algorithm: hash 算法
        files: 全部文件路径 tuple（去重后至少 2 个, 保持原顺序）
        files_with_meta: 与 files 一一对应的 DuplicateFile（size/mtime/ctime）
        hash_size: 文件内容的字节数（= 第一个文件 size, 全部相等）
        wasted_bytes: size × (N-1) — 浪费的空间
    """

    hash_value: str
    algorithm: str
    files: tuple[Path, ...]
    files_with_meta: tuple[DuplicateFile, ...] = ()
    hash_size: int = 0
    wasted_bytes: int = 0

    def __post_init__(self) -> None:
        # frozen 友好: 不能改 self.files, 但 len(files) 计算 cheap
        if len(self.files) < 2:
            raise ValueError(
                f"DuplicateGroup 必须至少 2 个文件, 实际 {len(self.files)}"
            )

    @property
    def keeper(self) -> Path:
        """保留的（最早修改时间的）."""
        if self.files_with_meta:
            return min(self.files_with_meta, key=lambda f: f.mtime).path
        return min(self.files, key=lambda f: f.stat().st_mtime)

    @property
    def duplicates(self) -> tuple[Path, ...]:
        """除 keeper 之外的所有文件."""
        keep = self.keeper
        return tuple(f for f in self.files if f != keep)

    @property
    def count(self) -> int:
        return len(self.files)


@dataclass
class DedupStats:
    """去重统计（GUI / 日志用）."""

    total_files: int = 0       # 扫描的全部文件
    duplicate_groups: int = 0  # 重复组数
    duplicate_files: int = 0   # 重复文件数（每组 - 1）
    wasted_bytes: int = 0      # 可清理的字节
    duration_ms: int = 0       # 扫描耗时

    @property
    def wasted_human(self) -> str:
        """浪费空间人类可读."""
        n = float(self.wasted_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    def to_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "duplicate_groups": self.duplicate_groups,
            "duplicate_files": self.duplicate_files,
            "wasted_bytes": self.wasted_bytes,
            "duration_ms": self.duration_ms,
        }


@dataclass
class Deduper:
    """去重器（W4 v3：增强 metadata 集成）.

    设计原则：
    - **只查**: 不动文件，输出 DuplicateGroup 给上层消费
    - **向后兼容**: W2 时代的 find_duplicates() / get_stats() 保留行为
    - **metadata 优先**: find_duplicates_with_meta() 是 W4 v3 主推 API
    - **跨平台**: _stat_safe() 兜底 os.stat 失败（文件被删/权限错）
    """

    algorithm: str = "md5"  # md5 | sha1 | sha256 | blake2b
    duplicate_dir_name: str = "_duplicates"

    # ----- 兼容 W2 API -----

    def find_duplicates(self, files: Iterable[Path]) -> list[DuplicateGroup]:
        """找出所有重复组（不含 metadata, 向后兼容 W2 测试）.

        Args:
            files: 文件列表
        Returns:
            重复组列表（每组至少 2 个文件, 按组大小降序）
        """
        # 不带 algorithm 标记, 老测试期望 hash_value 是 hex string
        hash_to_files: dict[str, list[Path]] = defaultdict(list)
        for f in files:
            if not f.is_file():
                continue
            h = file_hash(f, self.algorithm)
            hash_to_files[h].append(f)
        groups: list[DuplicateGroup] = []
        for h, fs in hash_to_files.items():
            if len(fs) > 1:
                groups.append(DuplicateGroup(
                    hash_value=h,
                    algorithm=self.algorithm,
                    files=tuple(fs),
                ))
        # 优先按文件数排序, 方便用户看
        groups.sort(key=lambda g: -len(g.files))
        return groups

    def get_stats(self, files: Iterable[Path]) -> dict[str, int]:
        """统计去重前后空间（兼容 W2 API）."""
        files_list = list(files)
        groups = self.find_duplicates(files_list)
        total_files = len(files_list)
        duplicates = sum(len(g.files) - 1 for g in groups)
        wasted_bytes = 0
        for g in groups:
            try:
                size = g.files[0].stat().st_size
            except OSError:
                size = 0
            wasted_bytes += size * (len(g.files) - 1)
        return {
            "total_files": total_files,
            "duplicates": duplicates,
            "wasted_bytes": wasted_bytes,
            "savings_bytes": wasted_bytes,
        }

    # ----- W4 v3 新 API -----

    def find_duplicates_with_meta(
        self, files: Iterable[Path]
    ) -> tuple[list[DuplicateGroup], DedupStats]:
        """找出所有重复组 + 带 metadata + 统计.

        Args:
            files: 文件列表
        Returns:
            (groups, stats) — groups 按浪费字节数降序
        """
        import time
        t0 = time.monotonic()

        files_list = [f for f in files if f.is_file()]

        # Step 1: hash
        hash_to_files: dict[str, list[Path]] = defaultdict(list)
        for f in files_list:
            try:
                h = file_hash(f, self.algorithm)
                hash_to_files[h].append(f)
            except OSError:
                # 文件被删/权限错, 跳过
                continue

        # Step 2: 构建 group（带 metadata）
        groups: list[DuplicateGroup] = []
        for h, fs in hash_to_files.items():
            if len(fs) < 2:
                continue
            # 一次性 stat 全部
            meta_list: list[DuplicateFile] = []
            for p in fs:
                st = _stat_safe(p)
                if st is None:
                    # fallback: 0/0
                    meta_list.append(DuplicateFile(path=p, size=0, mtime=0.0, ctime=0.0))
                else:
                    meta_list.append(DuplicateFile(
                        path=p,
                        size=st.st_size,
                        mtime=st.st_mtime,
                        ctime=st.st_ctime,
                    ))
            hash_size = meta_list[0].size
            wasted = hash_size * (len(meta_list) - 1)
            groups.append(DuplicateGroup(
                hash_value=h,
                algorithm=self.algorithm,
                files=tuple(fs),
                files_with_meta=tuple(meta_list),
                hash_size=hash_size,
                wasted_bytes=wasted,
            ))

        # 按浪费字节数降序（最该清理的先看）
        groups.sort(key=lambda g: -g.wasted_bytes)

        # Step 3: 统计
        stats = DedupStats(
            total_files=len(files_list),
            duplicate_groups=len(groups),
            duplicate_files=sum(g.count - 1 for g in groups),
            wasted_bytes=sum(g.wasted_bytes for g in groups),
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        return groups, stats


# ----- 内部 helper -----


def _stat_safe(path: Path):
    """跨平台 os.stat 兜底, 失败返 None (Windows 文件被占用时偶发)."""
    try:
        return path.stat()
    except (OSError, ValueError):
        return None
