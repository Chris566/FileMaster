"""去重后台 Worker（W4 v3：只查 + 表格预览）.

模式与 BatchWorker / ClassifyWorker / PreviewWorker 一致：
QObject + QThread + 协作式取消 + 进度信号。

W4 v3 范围（用户拍板）：
- 只查，不动文件
- 进度信号: progressed(percent, message)
- 完成信号: finished(groups, stats)
- 失败信号: failed(error_msg)
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from filemaster.core.dedup import (
    Deduper,
    DedupStats,
    DuplicateFile,
    DuplicateGroup,
)
from filemaster.utils.hash import file_hash


class DedupWorker(QObject):
    """去重 Worker（W4 v3：只查）.

    工作流程：
    1. 扫描源目录所有文件
    2. 计算每个文件 hash（边算边报进度）
    3. 按 hash 分组找出重复组
    4. 收集 metadata（size/mtime/ctime）
    5. 一次性 finished 信号回主线程
    """

    # 信号
    progressed = Signal(int, str)  # (percent, message)
    finished = Signal(list, object)  # (list[DuplicateGroup], DedupStats)
    failed = Signal(str)  # error_msg

    def __init__(
        self,
        source: Path,
        *,
        algorithm: str = "md5",
        recursive: bool = True,
    ) -> None:
        super().__init__()
        self._source = source
        self._algorithm = algorithm
        self._recursive = recursive
        self._cancel_requested = False

    def cancel(self) -> None:
        """请求取消（协作式, 下个文件前检查）."""
        self._cancel_requested = True

    def _scan_files(self) -> list[Path]:
        """扫描源目录文件."""
        if self._recursive:
            return sorted(p for p in self._source.rglob("*") if p.is_file())
        return sorted(p for p in self._source.iterdir() if p.is_file())

    def run(self) -> None:
        """执行入口（在线程中跑）."""
        t0 = time.monotonic()
        try:
            self.progressed.emit(5, f"扫描 {self._source} ...")
            files = self._scan_files()
            if not files:
                stats = DedupStats(total_files=0, duration_ms=0)
                self.finished.emit([], stats)
                return
            self.progressed.emit(15, f"找到 {len(files)} 个文件, 开始算 hash ...")

            # 算 hash (边算边报进度)
            hash_to_files: dict[str, list[Path]] = {}
            total = len(files)
            for i, f in enumerate(files, 1):
                if self._cancel_requested:
                    self.failed.emit("用户取消")
                    return
                try:
                    h = file_hash(f, self._algorithm)
                    hash_to_files.setdefault(h, []).append(f)
                except OSError as e:
                    # 单个文件失败不阻塞整体
                    self.progressed.emit(
                        15 + int(75 * i / total),
                        f"⚠️ 跳过 {f.name} ({e})",
                    )
                    continue
                if i % 10 == 0 or i == total:
                    pct = 15 + int(75 * i / total)
                    self.progressed.emit(pct, f"已 hash {i}/{total} 个文件")

            # 构建重复组 + metadata
            self.progressed.emit(92, "构建重复组 + 收集 metadata ...")
            groups: list[DuplicateGroup] = []
            wasted = 0
            for h, fs in hash_to_files.items():
                if len(fs) < 2:
                    continue
                meta_list: list[DuplicateFile] = []
                for p in fs:
                    try:
                        st = p.stat()
                        meta_list.append(
                            DuplicateFile(
                                path=p,
                                size=st.st_size,
                                mtime=st.st_mtime,
                                ctime=st.st_ctime,
                            )
                        )
                    except OSError:
                        meta_list.append(
                            DuplicateFile(path=p, size=0, mtime=0.0, ctime=0.0)
                        )
                hash_size = meta_list[0].size
                wasted_group = hash_size * (len(meta_list) - 1)
                groups.append(DuplicateGroup(
                    hash_value=h,
                    algorithm=self._algorithm,
                    files=tuple(m.path for m in meta_list),
                    files_with_meta=tuple(meta_list),
                    hash_size=hash_size,
                    wasted_bytes=wasted_group,
                ))
                wasted += wasted_group

            # 按浪费字节数降序
            groups.sort(key=lambda g: -g.wasted_bytes)

            stats = DedupStats(
                total_files=total,
                duplicate_groups=len(groups),
                duplicate_files=sum(g.count - 1 for g in groups),
                wasted_bytes=wasted,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

            self.progressed.emit(
                100,
                f"完成: {stats.duplicate_groups} 组, 浪费 {stats.wasted_human}",
            )
            self.finished.emit(groups, stats)

        except Exception as e:
            self.failed.emit(f"去重过程异常: {e}")


# 暴露一个 facade 函数, 跟核心 Deduper 行为一致
def find_duplicates_in_dir(
    source: Path,
    *,
    algorithm: str = "md5",
    recursive: bool = True,
) -> tuple[list[DuplicateGroup], DedupStats]:
    """同步版去重 (Worker 不便用时直接调这个).

    Returns:
        (groups, stats)
    """
    deduper = Deduper(algorithm=algorithm)
    if recursive:
        files = sorted(p for p in source.rglob("*") if p.is_file())
    else:
        files = sorted(p for p in source.iterdir() if p.is_file())
    return deduper.find_duplicates_with_meta(files)
