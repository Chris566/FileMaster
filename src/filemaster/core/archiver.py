"""压缩归档（W10 详细实现）.

按类型 / 时间 / 目录 分卷 zip。
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from filemaster.core.classifier import BUILTIN_CATEGORIES


@dataclass(frozen=True)
class ArchiveTask:
    """单次压缩任务."""

    source_files: tuple[Path, ...]
    archive_path: Path
    compression: int = zipfile.ZIP_DEFLATED
    compresslevel: int = 6


class Archiver:
    """归档器."""

    def archive(self, task: ArchiveTask) -> int:
        """执行压缩.

        Returns:
            写入的字节数（压缩后）
        """
        task.archive_path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with zipfile.ZipFile(
            task.archive_path, "w", task.compression, task.compresslevel
        ) as zf:
            for file in task.source_files:
                if not file.is_file():
                    continue
                zf.write(file, arcname=file.name)
                written += file.stat().st_size
        return written

    def archive_by_category(
        self, files: Iterable[Path], output_dir: Path
    ) -> dict[str, Path]:
        """按内置类型分卷压缩.

        Returns:
            {category: archive_path}
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        bucketed: dict[str, list[Path]] = {cat: [] for cat in BUILTIN_CATEGORIES}
        bucketed["OTHER"] = []
        for f in files:
            ext = f.suffix.lower()
            placed = False
            for cat, exts in BUILTIN_CATEGORIES.items():
                if ext in exts:
                    bucketed[cat].append(f)
                    placed = True
                    break
            if not placed:
                bucketed["OTHER"].append(f)
        results: dict[str, Path] = {}
        for cat, fs in bucketed.items():
            if not fs:
                continue
            archive_path = output_dir / f"{cat}.zip"
            self.archive(ArchiveTask(source_files=tuple(fs), archive_path=archive_path))
            results[cat] = archive_path
        return results
