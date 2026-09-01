"""压缩归档（W10 详细实现）.

按类型 / 时间 / 目录 分卷压缩。
支持格式: zip / tar.gz / tar.bz2。
W7 协作式取消 + W9 硬中断 (中途中断时关闭并删除半成品)。
"""

from __future__ import annotations

import contextlib
import tarfile
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from filemaster.core.classifier import BUILTIN_CATEGORIES
from filemaster.core.safe_rename import (
    SafeRenameResult,
    cleanup_orphan_tmps,
    make_tmp_path,
    safe_rename,
)


class ArchiveFormat(str, Enum):
    """支持的归档格式."""

    ZIP = "zip"
    TAR_GZ = "tar.gz"
    TAR_BZ2 = "tar.bz2"

    @property
    def extension(self) -> str:
        if self is ArchiveFormat.ZIP:
            return ".zip"
        if self is ArchiveFormat.TAR_GZ:
            return ".tar.gz"
        return ".tar.bz2"

    @classmethod
    def from_path(cls, path: Path) -> ArchiveFormat:
        """从文件路径推断格式 (按扩展名)."""
        name = path.name.lower()
        if name.endswith(".tar.gz"):
            return cls.TAR_GZ
        if name.endswith(".tar.bz2"):
            return cls.TAR_BZ2
        return cls.ZIP


@dataclass(frozen=True)
class ArchiveTask:
    """单次压缩任务."""

    source_files: tuple[Path, ...]
    archive_path: Path
    fmt: ArchiveFormat = ArchiveFormat.ZIP
    compression: int = 6  # zip: 0-9, tar.gz/bz2: 1-9
    base_dir: Path | None = None  # 归档内相对路径基准 (None = 用文件 basename)


@dataclass(frozen=True)
class ArchiveResult:
    """单次归档结果."""

    archive_path: Path
    source_count: int
    written_bytes: int  # 压缩前累计字节数
    elapsed: float  # 秒
    status: str  # "OK" | "CANCELLED" | "ERROR"
    message: str = ""


@dataclass
class ArchiveEntry:
    """单条归档记录 (用于撤销)."""

    archive_path: Path
    source_files: tuple[Path, ...]
    fmt: ArchiveFormat
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))


class Archiver:
    """归档器.

    用法:
        archiver = Archiver()
        result = archiver.archive_with_progress(
            files, output_dir / "backup.zip",
            on_progress=lambda i, t, f, b: ...,
            is_cancelled=lambda: token.is_cancelled,
        )
    """

    def archive(self, task: ArchiveTask) -> int:
        """同步压缩 (无进度回调, 适合测试 / 一次性脚本).

        Returns:
            写入的字节数 (压缩前累计源字节, 用于报告)
        """
        task.archive_path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        if task.fmt is ArchiveFormat.ZIP:
            with zipfile.ZipFile(
                task.archive_path, "w", zipfile.ZIP_DEFLATED, task.compression
            ) as zf:
                for file in task.source_files:
                    if not file.is_file():
                        continue
                    arcname = self._arcname(file, task.base_dir)
                    zf.write(file, arcname=arcname)
                    written += file.stat().st_size
        else:
            mode = "w:gz" if task.fmt is ArchiveFormat.TAR_GZ else "w:bz2"
            with tarfile.open(task.archive_path, mode, compresslevel=task.compression) as tf:
                for file in task.source_files:
                    if not file.is_file():
                        continue
                    arcname = self._arcname(file, task.base_dir)
                    tf.add(file, arcname=arcname, recursive=False)
                    written += file.stat().st_size
        return written

    def archive_with_progress(
        self,
        source_files: Iterable[Path],
        archive_path: Path,
        fmt: ArchiveFormat = ArchiveFormat.ZIP,
        compression: int = 6,
        base_dir: Path | None = None,
        on_progress: Callable[[int, int, Path, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ArchiveResult:
        """带进度 + 取消的归档.

        W7 协作式取消 (文件之间检查) + W9 硬中断 (归档内取消时关闭并删除半成品).

        Args:
            source_files: 要归档的文件
            archive_path: 归档输出路径 (含扩展名)
            fmt: 归档格式
            compression: 压缩级别 (0-9, 默认 6)
            base_dir: 归档内相对路径基准
            on_progress: 进度回调 (i, total, file, bytes_written)
            is_cancelled: 取消回调 (返回 True 时停止)

        Returns:
            ArchiveResult
        """
        start = time.monotonic()
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        files_list = [f for f in source_files if f.is_file()]
        total = len(files_list)
        if total == 0:
            return ArchiveResult(archive_path, 0, 0, 0.0, "ERROR", "无可归档文件")

        # 先写到 .filemaster.tmp.<8hex> 临时文件, 最后 atomic rename
        # W9 模式: 取消时直接关掉, tmp 文件被清理
        tmp_path = make_tmp_path(archive_path)
        written = 0
        cancelled = False
        last_i = 0

        try:
            if fmt is ArchiveFormat.ZIP:
                with zipfile.ZipFile(
                    tmp_path, "w", zipfile.ZIP_DEFLATED, compression
                ) as zf:
                    for i, file in enumerate(files_list, 1):
                        if is_cancelled is not None and is_cancelled():
                            cancelled = True
                            break
                        arcname = self._arcname(file, base_dir)
                        zf.write(file, arcname=arcname)
                        written += file.stat().st_size
                        last_i = i
                        if on_progress is not None:
                            with contextlib.suppress(Exception):
                                on_progress(i, total, file, written)
            else:
                mode = "w:gz" if fmt is ArchiveFormat.TAR_GZ else "w:bz2"
                with tarfile.open(tmp_path, mode, compresslevel=compression) as tf:
                    for i, file in enumerate(files_list, 1):
                        if is_cancelled is not None and is_cancelled():
                            cancelled = True
                            break
                        arcname = self._arcname(file, base_dir)
                        tf.add(file, arcname=arcname, recursive=False)
                        written += file.stat().st_size
                        last_i = i
                        if on_progress is not None:
                            with contextlib.suppress(Exception):
                                on_progress(i, total, file, written)

            if cancelled:
                # 取消: 关闭后删除 tmp (zipfile/tarfile 上下文管理器已关闭)
                with contextlib.suppress(OSError):
                    tmp_path.unlink()
                # last_i 是已成功写入的源文件数
                # last_i == 0: 还没写就被取消
                # last_i >= 1: 已写 last_i 个后, 第 last_i+1 个前被取消
                return ArchiveResult(
                    archive_path, last_i, written, time.monotonic() - start,
                    "CANCELLED", f"已取消, 已处理 {last_i}/{total} 个文件",
                )

            # 归档完成, atomic 移动到最终位置 (W9 模式: 用 safe_rename)
            safe_result: SafeRenameResult = safe_rename(
                tmp_path, archive_path, is_cancelled=is_cancelled
            )
            if safe_result.status == "ROLLBACK":
                return ArchiveResult(
                    archive_path, total, written, time.monotonic() - start,
                    "CANCELLED", "归档完成后取消, 已回滚",
                )
            if safe_result.status == "ERROR":
                return ArchiveResult(
                    archive_path, total, written, time.monotonic() - start,
                    "ERROR", f"原子移动失败: {safe_result.message}",
                )

        except (OSError, zipfile.BadZipFile, tarfile.TarError) as e:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            return ArchiveResult(
                archive_path, 0, 0, time.monotonic() - start, "ERROR", f"归档失败: {e}"
            )

        return ArchiveResult(
            archive_path, total, written, time.monotonic() - start, "OK", ""
        )

    def archive_by_category(
        self,
        files: Iterable[Path],
        output_dir: Path,
        fmt: ArchiveFormat = ArchiveFormat.ZIP,
        compression: int = 6,
        on_progress: Callable[[str, int, int, Path, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, ArchiveResult]:
        """按内置类型分卷压缩.

        Returns:
            {category: ArchiveResult}
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        bucketed: dict[str, list[Path]] = {cat: [] for cat in BUILTIN_CATEGORIES}
        bucketed["OTHER"] = []
        for f in files:
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            placed = False
            for cat, exts in BUILTIN_CATEGORIES.items():
                if ext in exts:
                    bucketed[cat].append(f)
                    placed = True
                    break
            if not placed:
                bucketed["OTHER"].append(f)
        results: dict[str, ArchiveResult] = {}
        for cat, fs in bucketed.items():
            if not fs:
                continue
            if is_cancelled is not None and is_cancelled():
                results[cat] = ArchiveResult(
                    output_dir / f"{cat}{fmt.extension}", 0, 0, 0.0, "CANCELLED",
                    "在分卷之间取消",
                )
                continue
            archive_path = output_dir / f"{cat}{fmt.extension}"

            def _cat_on_progress(
                i: int, t: int, f: Path, b: int, _cat: str = cat
            ) -> None:
                if on_progress is not None:
                    on_progress(_cat, i, t, f, b)

            result = self.archive_with_progress(
                fs, archive_path, fmt=fmt, compression=compression,
                on_progress=_cat_on_progress, is_cancelled=is_cancelled,
            )
            results[cat] = result
        return results

    @staticmethod
    def _arcname(file: Path, base_dir: Path | None) -> str:
        """计算归档内相对路径名."""
        if base_dir is not None:
            with contextlib.suppress(ValueError):
                return str(file.relative_to(base_dir))
        return file.name


# 入口清理 (worker 启动时调, 处理上次崩溃残留)
def cleanup_archive_tmps(directory: Path) -> int:
    """清理归档过程中可能留下的 .filemaster.tmp.* 残留 (复用 safe_rename 工具)."""
    return cleanup_orphan_tmps(directory)
