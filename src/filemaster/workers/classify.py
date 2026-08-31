"""分类后台 Worker（W4 v1）.

模式与 BatchWorker 一致：QObject + QThread + 协作式取消 + 进度信号。
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from filemaster.core.classifier import Classification, classify_batch


class ClassifyWorker(QObject):
    """分类 Worker.

    工作流程：
    1. 扫描源目录（或用传入文件列表）
    2. classify_batch 分类
    3. 按 Category 建子目录 + 复制（或移动）文件
    4. 进度 / 完成 / 失败信号回主线程
    """

    # 信号
    progressed = Signal(int, str)  # (percent, message)
    file_classified = Signal(object)  # Classification
    finished = Signal(list, str)  # (list[Classification], summary)
    failed = Signal(str, str)  # (file_path, error)

    def __init__(
        self,
        source: Path,
        destination: Path,
        *,
        mode: str = "copy",  # "copy" | "move"
        recursive: bool = True,
        dry_run: bool = False,
    ) -> None:
        super().__init__()
        self._source = source
        self._destination = destination
        self._mode = mode
        self._recursive = recursive
        self._dry_run = dry_run
        self._cancel_requested = False

    def cancel(self) -> None:
        """请求取消（协作式，下个文件前检查）."""
        self._cancel_requested = True

    def _scan_files(self) -> list[Path]:
        """扫描源目录文件."""
        if self._recursive:
            return sorted(p for p in self._source.rglob("*") if p.is_file())
        return sorted(p for p in self._source.iterdir() if p.is_file())

    def run(self) -> None:
        """执行入口（在线程中跑）."""
        try:
            files = self._scan_files()
            if not files:
                self.finished.emit([], "⚠️ 源目录无文件")
                return

            self.progressed.emit(5, f"扫描到 {len(files)} 个文件，开始分类…")

            # 1. 批量分类
            classifications = classify_batch(files)
            self.progressed.emit(
                40, f"分类完成：{len(classifications)} 个文件"
            )

            if self._cancel_requested:
                self.finished.emit(classifications, "⚠️ 用户取消（已分类，未复制）")
                return

            # 2. 按类别建子目录
            if not self._dry_run:
                self._destination.mkdir(parents=True, exist_ok=True)
                for c in classifications:
                    (self._destination / c.category.value).mkdir(
                        parents=True, exist_ok=True
                    )
            self.progressed.emit(60, "子目录已就绪")

            # 3. 复制 / 移动
            processed = 0
            for c in classifications:
                if self._cancel_requested:
                    break
                target = self._destination / c.category.value / c.source.name
                if self._dry_run:
                    processed += 1
                    self.file_classified.emit(c)
                    continue
                try:
                    if self._mode == "copy":
                        shutil.copy2(c.source, target)
                    else:  # move
                        shutil.move(str(c.source), str(target))
                    processed += 1
                    self.file_classified.emit(c)
                except OSError as e:
                    self.failed.emit(str(c.source), str(e))

                # 进度 60% → 100%
                pct = 60 + int(40 * processed / len(classifications))
                self.progressed.emit(pct, f"已处理 {processed}/{len(classifications)}")

            verb = "复制" if self._mode == "copy" else "移动"
            dry_tag = " (Dry Run)" if self._dry_run else ""
            summary = (
                f"✅ {verb}完成{dry_tag}：{processed}/{len(classifications)} 个文件"
                f" → {self._destination}"
            )
            self.finished.emit(classifications, summary)

        except Exception as e:
            self.failed.emit(str(self._source), f"分类过程异常: {e}")
            self.finished.emit([], f"❌ 失败: {e}")
