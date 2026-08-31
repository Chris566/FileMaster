"""去重后台 Worker（W4 v4：异步执行 move/delete/hardlink）.

W4 v4 Worker 模式（跟 W4 v3 DedupWorker 一致）：
QObject + QThread + 协作式取消 + 进度信号 + 3 个完成信号。

W4 v4 范围：
- move_duplicates / delete_duplicates / hardlink_duplicates 异步包装
- 进度信号: progressed(percent, message)
- 完成信号: finished(BatchActionResult)
- 失败信号: failed(error_msg)
- dry-run 透传
- 每文件粒度错误隔离（_run_action 内部不 raise, 失败写进 ActionResult）

W8 扩展：用 CancellationToken (W7) 替代 _cancel_requested bool,
新增 cancelled(int) 信号, 暴露 cancellation_token property.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from filemaster.core.cancellation import CancellationToken
from filemaster.core.dedup import (
    BatchActionResult,
    Deduper,
    DedupStats,
    DuplicateFile,
    DuplicateGroup,
    delete_duplicates,
    hardlink_duplicates,
    move_duplicates,
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

    W8 取消语义：cancel() 后, 下一个文件 hash 前检查 token.is_cancelled,
    立即退出, 触发 cancelled(processed_count) 信号 (而不是 failed).
    """

    # 信号
    progressed = Signal(int, str)  # (percent, message)
    finished = Signal(list, object)  # (list[DuplicateGroup], DedupStats)
    failed = Signal(str)  # error_msg
    cancelled = Signal(int)  # W8: 已处理的 hash 数 (让 UI 知道扫了多少)

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
        # W8: 协作式取消令牌, 替代 _cancel_requested bool
        self._token = CancellationToken()

    def cancel(self) -> None:
        """请求取消（协作式, 通过 CancellationToken 传给 run 内部检查）."""
        self._token.cancel()

    @property
    def cancellation_token(self) -> CancellationToken:
        """暴露 token 供外部 (测试 / 状态查询) 使用."""
        return self._token

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

            # 算 hash (边算边报进度, 边查取消)
            hash_to_files: dict[str, list[Path]] = {}
            total = len(files)
            processed = 0
            for i, f in enumerate(files, 1):
                # W8: 协作式取消 — 在文件之间检查, 不打断单文件 IO
                if self._token.is_cancelled:
                    self.cancelled.emit(processed)
                    return
                try:
                    h = file_hash(f, self._algorithm)
                    hash_to_files.setdefault(h, []).append(f)
                    processed += 1
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


# ----- W4 v4：DedupActionWorker -----


class DedupActionWorker(QObject):
    """去重动作 Worker（W4 v4：move / delete / hardlink 异步执行）.

    跟 DedupWorker 同样模式：QObject + QThread + 协作式取消。
    接受一个 DuplicateGroup, 跑一个动作, 出 BatchActionResult.

    W8 取消语义：cancel() 后, 下一个文件前检查 token.is_cancelled,
    已处理的文件结果保留, 触发 cancelled(processed_count) 信号.
    """

    progressed = Signal(int, str)          # (percent, message)
    finished = Signal(object)              # (BatchActionResult)
    failed = Signal(str)                   # error_msg
    cancelled = Signal(int)                # W8: 已处理的文件数

    def __init__(
        self,
        group: DuplicateGroup,
        action: str,
        *,
        target_dir: Path | None = None,
        dry_run: bool = False,
        overwrite: bool = False,
        use_trash: bool = True,
    ) -> None:
        super().__init__()
        self._group = group
        self._action = action  # "move" | "delete" | "hardlink"
        self._target_dir = target_dir
        self._dry_run = dry_run
        self._overwrite = overwrite
        self._use_trash = use_trash
        # W8: 协作式取消令牌
        self._token = CancellationToken()

    def cancel(self) -> None:
        """请求取消（W8: 通过 CancellationToken）."""
        self._token.cancel()

    @property
    def cancellation_token(self) -> CancellationToken:
        """暴露 token 供外部状态查询."""
        return self._token

    def run(self) -> None:
        try:
            self.progressed.emit(0, f"开始 {self._action} (dry_run={self._dry_run}) ...")
            t0 = time.monotonic()
            total = len(self._group.duplicates)
            if total == 0:
                self.failed.emit("该组没有重复文件（只有 keeper）")
                return

            # 每文件执行, 单文件错误隔离
            results: list = []
            undo_entries: list[dict] = []

            for i, src in enumerate(self._group.duplicates, 1):
                # W8: 协作式取消 — 在文件之间检查
                if self._token.is_cancelled:
                    self.cancelled.emit(i - 1)  # i-1 = 已完成文件数
                    return

                # 单文件动作 - 包 try/except 防止 OSError 击穿
                file_result = self._run_single(src)
                results.append(file_result)
                if file_result.success and not self._dry_run:
                    undo_entries.append(self._undo_for(src))

                pct = int(100 * i / total)
                status = "✓" if file_result.success else "✗"
                self.progressed.emit(
                    pct,
                    f"{status} {self._action} {i}/{total}: {src.name}",
                )

            # 写 undo log
            undo_log_path = None
            if not self._dry_run and self._action != "hardlink" and undo_entries:
                undo_log_path = self._write_undo_log(undo_entries)

            from filemaster.core.dedup import BatchActionResult
            batch = BatchActionResult(
                group=self._group,
                action=self._action,
                dry_run=self._dry_run,
                results=results,
                undo_log_path=undo_log_path,
            )

            duration_ms = int((time.monotonic() - t0) * 1000)
            self.progressed.emit(
                100,
                f"{self._action} 完成: 成功 {batch.success_count}/{total}, "
                f"失败 {batch.fail_count}, 耗时 {duration_ms} ms",
            )
            self.finished.emit(batch)
        except Exception as e:
            self.failed.emit(f"{self._action} 异常: {e}")

    def _run_single(self, src: Path):
        """对 group 跑对应动作, 过滤出 src 的单文件结果.

        不用 DuplicateGroup 单文件重建, 直接跑整个 group, 找到 src 对应的
        ActionResult 返出来 (避免 hardlink 边界: src.mtime < keeper.mtime 时
        src 会被选为新 keeper, duplicates 为空, 函数返空 batch).
        """
        from filemaster.core.dedup import ActionResult

        if self._action == "move":
            if self._dry_run:
                target = (self._target_dir or (self._group.keeper.parent / "_duplicates")) / src.name
                return ActionResult(source=src, target=target, action="move", success=True, dry_run=True)
            batch = move_duplicates(self._group, self._target_dir, dry_run=False, overwrite=self._overwrite)
        elif self._action == "delete":
            if self._dry_run:
                return ActionResult(source=src, target=None, action="delete", success=True, dry_run=True)
            batch = delete_duplicates(self._group, dry_run=False, use_trash=self._use_trash)
        elif self._action == "hardlink":
            if self._dry_run:
                return ActionResult(source=src, target=src, action="hardlink", success=True, dry_run=True)
            batch = hardlink_duplicates(self._group, dry_run=False, overwrite=self._overwrite)
        else:
            return ActionResult(
                source=src, target=None, action=self._action,
                success=False, error=f"未知 action: {self._action}", dry_run=False,
            )

        # 找 src 对应的那条结果
        for r in batch.results:
            try:
                if r.source.resolve() == src.resolve():
                    return r
            except OSError:
                if str(r.source) == str(src):
                    return r
        # 兜底：没找到 → 返 fail
        return ActionResult(
            source=src, target=None, action=self._action,
            success=False, error="no result returned", dry_run=False,
        )

    def _undo_for(self, src: Path) -> dict:
        """生成单文件 undo 条目."""
        if self._action == "move":
            target = (self._target_dir or (self._group.keeper.parent / "_duplicates")) / src.name
            return {"op": "move", "from": str(target), "to": str(src)}
        elif self._action == "delete":
            return {"op": "trash" if self._use_trash else "delete", "path": str(src)}
        elif self._action == "hardlink":
            return {"op": "hardlink", "path": str(src), "keeper": str(self._group.keeper)}
        return {"op": "unknown"}

    def _write_undo_log(self, entries: list[dict]) -> Path | None:
        """复用 core.dedup._write_undo_log 一样的逻辑."""
        import json
        try:
            home = Path.home() / ".filemaster" / "undo"
            home.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = home / f"{ts}_{self._group.hash_value[:8]}_{self._action}.json"
            payload = {
                "action": self._action,
                "timestamp": ts,
                "group_hash": self._group.hash_value,
                "keeper": str(self._group.keeper),
                "entries": entries,
            }
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            return path
        except OSError:
            return None


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
