"""硬中断安全的 rename 操作 (W9).

W7 的 CancellationToken 是"协作式" — 引擎在文件之间检查 cancel,
但单文件操作 (os.replace) 不可中断. 大文件场景下用户点取消后还要
等几秒 (mv 大文件 + 原子 replace), 体验差.

W9 把单文件 rename 拆成两步, 让取消生效:
  - Step A: shutil.move(src, src+".filemaster.tmp.<hash>")
    同卷是 rename (瞬时), 跨卷是 copy+delete (慢但可接受, 跟 W7 一样)
  - 检查 is_cancelled
  - 如取消 → shutil.move(tmp, src)  回滚
  - Step B: os.replace(tmp, dst)  原子覆盖

取消时:
  - 不写 UndoEntry (因为没真正完成 rename)
  - ROLLBACK 状态 (源文件还在原位)
  - 失败时返回 ERROR (源文件保留 + 残留 .tmp, worker 需扫目录清理)

典型用法:
    result = safe_rename(src, dst, is_cancelled=lambda: token.is_cancelled)
    if result.status == "OK":
        undo_stack.push(...)
    elif result.status == "ROLLBACK":
        # 取消, 不入栈
    else:  # ERROR
        # 清理残留 .tmp
        cleanup_orphan_tmps(src.parent)
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# 临时文件名后缀: .filemaster.tmp.<8字符 hash>
# 用 8 字符 md5 避免长名 (Windows MAX_PATH 260)
_TMP_SUFFIX_PATTERN = re.compile(r"\.filemaster\.tmp\.[0-9a-f]{8}$")


@dataclass(frozen=True)
class SafeRenameResult:
    """safe_rename 返回值."""

    source: Path
    target: Path | None
    status: str  # "OK" | "ROLLBACK" | "ERROR"
    message: str = ""


def make_tmp_path(src: Path) -> Path:
    """计算 .filemaster.tmp.<hash> 临时路径.

    短 hash 基于 (inode, mtime_ns) — 同名文件会得到不同 hash, 避免覆盖残留.
    """
    try:
        stat = src.stat()
        seed = f"{stat.st_ino}-{stat.st_mtime_ns}-{stat.st_size}"
    except OSError:
        # 源文件已被删 (极罕见) — 用 name 做兜底
        seed = src.name
    short = hashlib.md5(seed.encode("utf-8")).hexdigest()[:8]
    return src.with_name(src.name + f".filemaster.tmp.{short}")


def safe_rename(
    src: Path,
    dst: Path,
    is_cancelled: Callable[[], bool] | None = None,
) -> SafeRenameResult:
    """硬中断安全的 rename — 可在 Step A 完成后响应取消.

    Args:
        src: 源文件
        dst: 目标文件
        is_cancelled: 可选, 返回 True 时停止. 取消发生在 Step A 完成后,
                      Step B (os.replace 原子) 不可中断, 但同卷极快 (微秒级).
    Returns:
        SafeRenameResult:
          - status="OK": 成功, dst 已是新文件
          - status="ROLLBACK": 取消, src 仍在原位 (没动)
          - status="ERROR": 失败, src 可能已动, 残留 .tmp 需清理
    """
    if not src.exists():
        return SafeRenameResult(src, dst, "ERROR", f"源文件不存在: {src.name}")

    tmp = make_tmp_path(src)

    # Step A: src -> tmp
    try:
        shutil.move(str(src), str(tmp))
    except OSError as e:
        return SafeRenameResult(src, dst, "ERROR", f"Step A 失败: {e}")

    # 中断检查点 (W9 关键) — 取消时回滚
    if is_cancelled is not None and is_cancelled():
        try:
            shutil.move(str(tmp), str(src))
        except OSError as e:
            # 回滚失败, 留 .tmp 残留, 调用方需 cleanup_orphan_tmps
            return SafeRenameResult(
                src, dst, "ERROR",
                f"回滚失败, 残留临时文件 {tmp.name}: {e}",
            )
        return SafeRenameResult(src, dst, "ROLLBACK", "Step A 后取消, 已回滚")

    # Step B: tmp -> dst (原子, 不可中断, 但同卷极快)
    try:
        os.replace(tmp, dst)
    except OSError as e:
        # Step B 失败, 尝试把 tmp 移回 src
        rollback_err: str | None = None
        try:
            shutil.move(str(tmp), str(src))
        except OSError as e2:
            rollback_err = f"; 回滚失败 {tmp.name}: {e2}"
        msg = f"Step B 失败: {e}"
        if rollback_err:
            msg += rollback_err
        return SafeRenameResult(src, dst, "ERROR", msg)

    return SafeRenameResult(src, dst, "OK", "")


def find_orphan_tmps(directory: Path) -> list[Path]:
    """递归扫描目录, 找出所有 .filemaster.tmp.<hash> 残留.

    用场景: 取消后回滚失败 / worker 崩溃 / 进程被杀, 留有 .tmp 残留.
    Worker 启动时调一次清理, 或在 cancelled 收尾时清理.
    递归扫: 进程可能死在子目录 (重命名跨目录场景).
    """
    if not directory.is_dir():
        return []
    orphans: list[Path] = []
    try:
        # os.walk 默认 topdown=True, 可以安全 prune
        for root, _dirs, files in os.walk(directory):
            root_path = Path(root)
            for name in files:
                if _TMP_SUFFIX_PATTERN.search(name):
                    orphans.append(root_path / name)
    except OSError:
        return []
    return orphans


def cleanup_orphan_tmps(directory: Path) -> int:
    """删除 .filemaster.tmp.* 残留文件. 返回删除数量."""
    orphans = find_orphan_tmps(directory)
    removed = 0
    for p in orphans:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed
