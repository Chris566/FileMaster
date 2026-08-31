"""文件去重（W4 v4：只查 + 移动/删除/硬链接）.

按 MD5 / SHA1 / SHA256 / BLAKE2B 哈希分组,重复文件展示在 GUI 表格里。

W4 v3 范围：只查 + 表格预览 (W4 v3)。
W4 v4 范围(本版)：
  - 3 个动作函数：move_duplicates / delete_duplicates / hardlink_duplicates
  - 每文件粒度错误隔离（一个失败不阻塞整批）
  - dry-run 支持（只列将要做什么, 不真动）
  - 跨平台兼容：Windows 硬链 / 跨设备移动 / 权限错 都有明确报错
  - undo log（move/delete 写 JSON, hardlink 不写因为无数据丢失）

API 设计原则：
  - 同步函数, 全部接收 DuplicateGroup 返回 ActionResult
  - 异步执行由 DedupActionWorker 包装
  - dry_run=True 时所有函数只返回"将要做什么",不调用 shutil/os.remove
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import time
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
    """去重器（W4 v4：只查 + 3 动作函数入口）.

    设计原则：
    - **只查**: 不动文件, 输出 DuplicateGroup 给上层消费
    - **向后兼容**: W2 时代的 find_duplicates() / get_stats() 保留行为
    - **metadata 优先**: find_duplicates_with_meta() 是 W4 v3 主推 API
    - **跨平台**: _stat_safe() 兜底 os.stat 失败（文件被删/权限错）
    - **3 动作走模块函数**: move/delete/hardlink 不放这里, 见模块末尾
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


# ----- W4 v4：3 个动作函数 + ActionResult -----


@dataclass
class ActionResult:
    """单文件动作结果.

    Attributes:
        source: 动作执行的文件原路径
        target: 动作后的文件路径（move 后 / hardlink 后 / delete 后为 None）
        action: 动作名 ("move" | "delete" | "hardlink")
        success: 是否成功
        error: 失败时的错误信息, 成功时为 None
        dry_run: True 表示只看了没真动
    """

    source: Path
    target: Path | None
    action: str
    success: bool
    error: str | None = None
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "source": str(self.source),
            "target": str(self.target) if self.target else None,
            "action": self.action,
            "success": self.success,
            "error": self.error,
            "dry_run": self.dry_run,
        }


@dataclass
class BatchActionResult:
    """一组动作的批结果.

    Attributes:
        group: 该批对应的 DuplicateGroup
        action: 动作名
        dry_run: True 表示只看了没真动
        results: 每文件的 ActionResult
        undo_log_path: 写出的 undo 日志路径（move/delete 才有, hardlink 是 None）
    """

    group: DuplicateGroup
    action: str
    dry_run: bool
    results: list[ActionResult] = field(default_factory=list)
    undo_log_path: Path | None = None

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.success)

    @property
    def saved_bytes(self) -> int:
        """动作实际节省的字节数（= 成功处理的 duplicate 文件 × hash_size）."""
        return self.group.hash_size * self.success_count


# ----- move -----


def move_duplicates(
    group: DuplicateGroup,
    target_dir: Path | None = None,
    *,
    dry_run: bool = False,
    overwrite: bool = False,
) -> BatchActionResult:
    """把 group.duplicates 移到 target_dir.

    Args:
        group: 重复组
        target_dir: 目标目录, None 时用 group.duplicates[0].parent / _duplicates
        dry_run: True 时只列将要做什么, 不真动
        overwrite: True 时已存在的目标文件会被覆盖, False 时同名跳过
    Returns:
        BatchActionResult
    """
    if target_dir is None:
        # 默认: keeper 所在目录的 _duplicates/ 子目录
        target_dir = group.keeper.parent / "_duplicates"

    results: list[ActionResult] = []
    undo_entries: list[dict] = []

    if not dry_run:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # 整个批失败：每个文件都返同样错
            return BatchActionResult(
                group=group,
                action="move",
                dry_run=False,
                results=[
                    ActionResult(
                        source=src,
                        target=None,
                        action="move",
                        success=False,
                        error=f"无法创建目标目录 {target_dir}: {e}",
                        dry_run=False,
                    )
                    for src in group.duplicates
                ],
            )

    for src in group.duplicates:
        target = target_dir / src.name
        if dry_run:
            results.append(ActionResult(
                source=src,
                target=target,
                action="move",
                success=True,
                dry_run=True,
            ))
            continue

        # 目标已存在且不覆盖 → 跳过
        if target.exists() and not overwrite:
            results.append(ActionResult(
                source=src,
                target=target,
                action="move",
                success=False,
                error=f"目标已存在: {target}",
                dry_run=False,
            ))
            continue

        # 同一文件 → 跳过（不需要移）
        try:
            if src.resolve() == target.resolve():
                results.append(ActionResult(
                    source=src,
                    target=target,
                    action="move",
                    success=True,
                    error=None,
                    dry_run=False,
                ))
                continue
        except OSError:
            pass

        try:
            # 跨设备自动 fallback: move() 在同设备是 rename, 跨设备复制+删除
            shutil.move(str(src), str(target))
            results.append(ActionResult(
                source=src,
                target=target,
                action="move",
                success=True,
                dry_run=False,
            ))
            undo_entries.append({
                "op": "move",
                "from": str(target),
                "to": str(src),
            })
        except OSError as e:
            results.append(ActionResult(
                source=src,
                target=target,
                action="move",
                success=False,
                error=str(e),
                dry_run=False,
            ))

    undo_log_path = _write_undo_log(group, "move", undo_entries, dry_run)
    return BatchActionResult(
        group=group,
        action="move",
        dry_run=dry_run,
        results=results,
        undo_log_path=undo_log_path,
    )


# ----- delete -----


def delete_duplicates(
    group: DuplicateGroup,
    *,
    dry_run: bool = False,
    use_trash: bool = True,
) -> BatchActionResult:
    """删 group.duplicates（不动 keeper）.

    Args:
        group: 重复组
        dry_run: True 时只列将要做什么
        use_trash: True 时移到回收站（send2trash 库, 跨平台）, False 时真删
    Returns:
        BatchActionResult
    """
    results: list[ActionResult] = []
    undo_entries: list[dict] = []

    for src in group.duplicates:
        if dry_run:
            results.append(ActionResult(
                source=src,
                target=None,
                action="delete",
                success=True,
                dry_run=True,
            ))
            continue

        try:
            if use_trash:
                _safe_send2trash(src)
            else:
                os.remove(src)
            results.append(ActionResult(
                source=src,
                target=None,
                action="delete",
                success=True,
                dry_run=False,
            ))
            undo_entries.append({
                "op": "delete" if not use_trash else "trash",
                "path": str(src),
            })
        except OSError as e:
            results.append(ActionResult(
                source=src,
                target=None,
                action="delete",
                success=False,
                error=str(e),
                dry_run=False,
            ))

    undo_log_path = _write_undo_log(group, "delete", undo_entries, dry_run)
    return BatchActionResult(
        group=group,
        action="delete",
        dry_run=dry_run,
        results=results,
        undo_log_path=undo_log_path,
    )


# ----- hardlink -----


def hardlink_duplicates(
    group: DuplicateGroup,
    *,
    dry_run: bool = False,
    overwrite: bool = False,
) -> BatchActionResult:
    """用硬链接替换 duplicates（指向 keeper, 节省空间且保留访问路径）.

    原理：删原文件 → os.link(keeper, 原路径), 这样所有"重复"实际是同一 inode。
    优点：删一个不影响其他路径访问, 改一处全改（不同副本会同步）。
    注意：Windows 上 os.link 需要同卷 + 经常需要管理员/开发者模式；失败给明确报错。

    Args:
        group: 重复组
        dry_run: True 时只列将要做什么
        overwrite: True 时已存在的目标会被替换, False 时同名跳过
    Returns:
        BatchActionResult
    """
    keeper = group.keeper
    platform_name = platform.system()  # "Windows" / "Linux" / "Darwin"
    results: list[ActionResult] = []
    undo_entries: list[dict] = []

    for src in group.duplicates:
        if dry_run:
            results.append(ActionResult(
                source=src,
                target=src,  # 硬链后路径不变
                action="hardlink",
                success=True,
                dry_run=True,
            ))
            continue

        # src 就是 keeper 的 path → 跳过
        try:
            if src.resolve() == keeper.resolve():
                results.append(ActionResult(
                    source=src,
                    target=src,
                    action="hardlink",
                    success=True,
                    dry_run=False,
                ))
                continue
        except OSError:
            pass

        # 删原文件 → 建硬链
        # 注: 硬链的目的地就是 src 本身, 不需要 overwrite 检查
        # 流程: os.remove(src) → os.link(keeper, src), src 路径最终指向 keeper 的 inode
        try:
            os.remove(src)
            os.link(keeper, src)
            results.append(ActionResult(
                source=src,
                target=src,
                action="hardlink",
                success=True,
                dry_run=False,
            ))
            # undo: 删硬链 + 把 keeper 复制回 src 路径
            # 但硬链和原文件是同一 inode, "撤销"实际是复制一份
            # 为了简单, undo 标 hardlink 反向 = unlink + copy
            undo_entries.append({
                "op": "hardlink",
                "path": str(src),
                "keeper": str(keeper),
            })
        except OSError as e:
            # 跨平台常见错: Windows 跨卷 / 权限不够 / 文件被占用
            hint = ""
            if platform_name == "Windows":
                hint = (
                    " (Windows 上硬链需: 同卷 + 管理员/开发者模式, "
                    "或文件已被其他进程占用)"
                )
            results.append(ActionResult(
                source=src,
                target=src,
                action="hardlink",
                success=False,
                error=f"{e}{hint}",
                dry_run=False,
            ))

    # hardlink 不写 undo log（无数据丢失, 但能反链回独立副本需要复制, 不在这里写）
    return BatchActionResult(
        group=group,
        action="hardlink",
        dry_run=dry_run,
        results=results,
        undo_log_path=None,
    )


# ----- 内部 helper -----


def _stat_safe(path: Path):
    """跨平台 os.stat 兜底, 失败返 None (Windows 文件被占用时偶发)."""
    try:
        return path.stat()
    except (OSError, ValueError):
        return None


def _safe_send2trash(path: Path) -> None:
    """调 send2trash 移回收站, 没装就 raise ImportError 引导装包."""
    try:
        from send2trash import send2trash  # type: ignore[import-not-found]
    except ImportError as e:
        raise ImportError(
            "需要 send2trash 库才能移到回收站, 装: pip install send2trash"
        ) from e
    send2trash(str(path))


def _write_undo_log(
    group: DuplicateGroup,
    action: str,
    entries: list[dict],
    dry_run: bool,
) -> Path | None:
    """写 undo 日志 (move/delete 成功条目).

    文件位置: <user_home>/.filemaster/undo/<timestamp>_<hash>_<action>.json

    不写: dry_run / 失败 / hardlink
    """
    if dry_run or action == "hardlink" or not entries:
        return None
    try:
        home = Path.home() / ".filemaster" / "undo"
        home.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = home / f"{ts}_{group.hash_value[:8]}_{action}.json"
        payload = {
            "action": action,
            "timestamp": ts,
            "group_hash": group.hash_value,
            "keeper": str(group.keeper),
            "entries": entries,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
    except OSError:
        return None

# ============================================================
# W4 v5: undo log 恢复
# ============================================================


@dataclass
class UndoLog:
    """W4 v4 写出的 undo log 描述.

    对应 JSON:
        {
            "action": "move" | "delete",
            "timestamp": "20260831_123456",
            "group_hash": "abc123...",
            "keeper": "/path/to/keeper",
            "entries": [
                {"op": "move", "from": "...", "to": "..."},
                {"op": "delete" | "trash", "path": "..."},
                ...
            ],
        }
    """

    path: Path
    action: str
    timestamp: str
    group_hash: str
    keeper: str
    entries: list[dict] = field(default_factory=list)

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def can_restore(self) -> bool:
        """只有 move 操作能反移回原位置. delete/trash 是单向的."""
        return self.action == "move" and all(
            e.get("op") == "move" for e in self.entries
        )

    @classmethod
    def from_path(cls, path: Path) -> UndoLog:
        """从 JSON 文件读 UndoLog. 损坏抛 ValueError."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            action=data.get("action", "?"),
            timestamp=data.get("timestamp", ""),
            group_hash=data.get("group_hash", ""),
            keeper=data.get("keeper", ""),
            entries=data.get("entries", []),
        )


@dataclass
class RestoreResult:
    """单文件恢复结果."""

    source: Path
    target: Path
    success: bool
    error: str | None = None
    skipped: bool = False


def _undo_log_dir() -> Path:
    """undo log 默认目录: <home>/.filemaster/undo/."""
    return Path.home() / ".filemaster" / "undo"


def list_undo_logs(log_dir: Path | None = None) -> list[UndoLog]:
    """列出所有 undo log (按时间倒序, 最新的在前).

    Args:
        log_dir: 自定义目录, None 时用 ~/.filemaster/undo/
    Returns:
        UndoLog 列表; 损坏的 JSON 会被跳过.
    """
    d = log_dir or _undo_log_dir()
    if not d.exists() or not d.is_dir():
        return []
    results: list[UndoLog] = []
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            results.append(UndoLog.from_path(p))
        except (json.JSONDecodeError, KeyError, OSError, ValueError):
            continue
    return results


def restore_undo_log(
    log_path: Path,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> list[RestoreResult]:
    """从 undo log JSON 恢复文件 (反向 move 操作).

    Args:
        log_path: undo log JSON 文件路径
        overwrite: True 时如果目标已存在则覆盖, False 时跳过
        dry_run: True 时只报告将要做什么, 不真动
    Returns:
        每文件的 RestoreResult
    Raises:
        ValueError: undo log 损坏或 action 不支持恢复
        FileNotFoundError: log_path 不存在
    """
    if not log_path.exists():
        raise FileNotFoundError(f"undo log 不存在: {log_path}")
    log = UndoLog.from_path(log_path)
    return _restore_undo_log(log, overwrite=overwrite, dry_run=dry_run)


def _restore_undo_log(
    log: UndoLog,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> list[RestoreResult]:
    """实际执行恢复 (UndoLog 内存对象)."""
    if log.action not in ("move", "delete"):
        raise ValueError(f"action={log.action} 不支持恢复 (只有 move/delete)")
    if log.action == "delete":
        raise ValueError(
            "delete 操作不可恢复 (文件已永久删除或已在回收站). "
            "请用专业恢复工具 (testdisk/photorec) 或从备份还原."
        )

    results: list[RestoreResult] = []
    for entry in log.entries:
        op = entry.get("op")
        if op != "move":
            continue
        src = Path(entry["from"])
        dst = Path(entry["to"])

        if dry_run:
            results.append(RestoreResult(
                source=src, target=dst, success=True, skipped=False,
            ))
            continue

        if not src.exists():
            results.append(RestoreResult(
                source=src, target=dst, success=False,
                error=f"源文件不存在: {src}",
            ))
            continue

        if dst.exists() and not overwrite:
            results.append(RestoreResult(
                source=src, target=dst, success=False, skipped=True,
                error=f"目标已存在: {dst}",
            ))
            continue

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            results.append(RestoreResult(
                source=src, target=dst, success=True,
            ))
        except OSError as e:
            results.append(RestoreResult(
                source=src, target=dst, success=False,
                error=str(e),
            ))

    return results
