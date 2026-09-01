"""命令行接口.

W4 v1-v5: classify / dedup-scan / dedup-action / dedup-undo
W5: rename 真实集成（接 Renamer + 进度回调 + 3 冲突策略 + dry-run + JSON）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
from pathlib import Path

from filemaster.core.classifier import (
    Category,
    classify_batch,
    group_by_category,
)


# ============================================================
# W4 v1 fix: Windows CI runner 默认 cp1252，emoji/中文 print 必炸
# 8/30 在 smoke 脚本立过同款问题（MEMORY.md），这次漏了 CLI 子进程入口。
# 双保险：
#   1. reconfigure(encoding="utf-8", errors="replace")
#   2. reconfigure 后 encoding 仍非 utf-8（Windows console 偶发不生效），
#      强制用 TextIOWrapper 替换 sys.stdout/sys.stderr
# 同步覆盖 sys.__stdout__ / sys.__stderr__（argparse 内部用 __stdout__）。
# Linux/macOS 默认就 utf-8，全是 no-op，不影响行为。
# ============================================================
def _ensure_utf8_io() -> None:
    import io

    for stream_name in ("stdout", "stderr"):
        private_name = f"__{stream_name}__"
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue

        ok = False
        # 1) 尝试 reconfigure
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
                if getattr(stream, "encoding", None) == "utf-8":
                    ok = True
            except Exception:
                pass

        if ok:
            continue

        # 2) reconfigure 失败 → 强制 TextIOWrapper 替换
        buf = getattr(stream, "buffer", None) or getattr(stream, "raw", None)
        if buf is None:
            continue
        try:
            new_stream = io.TextIOWrapper(
                buf,
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
                write_through=False,
            )
            setattr(sys, stream_name, new_stream)
            setattr(sys, private_name, new_stream)
        except Exception:
            pass


_ensure_utf8_io()

# ============================================================
# classify 子命令
# ============================================================


def _cmd_classify(args: argparse.Namespace) -> int:
    """分类子命令主逻辑.

    流程：扫描路径 → 批量分类 → 按选项展示/复制/移动
    """
    source = Path(args.source).resolve()
    if not source.exists():
        print(f"❌ 源路径不存在: {source}", file=sys.stderr)
        return 1
    if not source.is_dir():
        print(f"❌ 源路径不是目录: {source}", file=sys.stderr)
        return 1

    # 1. 扫描文件
    if args.recursive:
        files = sorted(p for p in source.rglob("*") if p.is_file())
    else:
        files = sorted(p for p in source.iterdir() if p.is_file())

    if not files:
        print(f"⚠️  目录无文件: {source}")
        return 0

    # 2. 批量分类
    results = classify_batch(files)

    # 3. 按选项输出
    if args.json:
        return _output_json(results)
    if args.copy:
        return _output_copy(results, Path(args.copy), args.dry_run)
    if args.move:
        return _output_move(results, Path(args.move), args.dry_run)

    # 默认：按选项展示
    if args.group or args.by_category:
        return _output_grouped(results, args.by_category)
    return _output_list(results, source)


def _output_list(results: list, source_root: Path) -> int:
    """默认输出：每行一个文件 + 分类 + 置信度."""
    print(f"📁 分类结果（共 {len(results)} 个文件）")
    print(f"   源目录：{source_root}\n")
    print(f"   {'分类':<10}  {'置信度':<8}  {'方法':<10}  路径")
    print(f"   {'-' * 10}  {'-' * 8}  {'-' * 10}  {'-' * 40}")
    for c in results:
        try:
            rel = c.source.relative_to(source_root)
        except ValueError:
            rel = c.source
        print(
            f"   {c.category.value:<10}  {c.confidence:<8.3f}  "
            f"{c.method.value:<10}  {rel}"
        )
    return 0


def _output_grouped(results: list, by_category: bool) -> int:
    """按类别分组输出."""
    groups = group_by_category(results)
    print(f"📁 按类别分组（共 {len(results)} 个文件 / {len(groups)} 类）\n")
    for cat in Category:
        if cat not in groups:
            continue
        items = groups[cat]
        print(f"  {cat.value} ({cat.label_zh}) — {len(items)} 个文件")
        if by_category:
            for c in items:
                print(f"    • {c.source.name}  (conf={c.confidence:.2f})")
        print()
    return 0


def _output_json(results: list) -> int:
    """JSON 输出（便于脚本管道）."""
    payload = {
        "total": len(results),
        "items": [c.to_dict() for c in results],
    }
    # 按 category 聚合统计
    by_cat: dict[str, int] = {}
    for c in results:
        by_cat[c.category.value] = by_cat.get(c.category.value, 0) + 1
    payload["summary"] = by_cat
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _output_copy(results: list, dest_root: Path, dry_run: bool) -> int:
    """按类别复制到子目录."""
    return _output_copy_move(results, dest_root, dry_run, copy_mode=True)


def _output_move(results: list, dest_root: Path, dry_run: bool) -> int:
    """按类别移动到子目录."""
    return _output_copy_move(results, dest_root, dry_run, copy_mode=False)


def _output_copy_move(
    results: list, dest_root: Path, dry_run: bool, copy_mode: bool
) -> int:
    """复制/移动共实现."""
    groups = group_by_category(results)
    if not dry_run:
        dest_root.mkdir(parents=True, exist_ok=True)

    verb = "复制" if copy_mode else "移动"
    action = "📋" if copy_mode else "🚚"
    print(f"{action} 准备{verb} {len(results)} 个文件 → {dest_root}")
    if dry_run:
        print("   ⚠️  dry-run 模式，不会真复制/移动\n")
    else:
        print()

    copied = 0
    for cat, items in groups.items():
        if not dry_run:
            (dest_root / cat.value).mkdir(parents=True, exist_ok=True)
        for c in items:
            target = dest_root / cat.value / c.source.name
            if dry_run:
                print(f"   {verb} [{cat.value}] {c.source.name}")
            else:
                try:
                    if copy_mode:
                        shutil.copy2(c.source, target)
                    else:
                        shutil.move(str(c.source), str(target))
                    copied += 1
                except OSError as e:
                    print(f"   ❌ 失败：{c.source.name} - {e}", file=sys.stderr)

    if not dry_run:
        print(f"\n✅ 完成：{verb} {copied} 个文件")
    return 0


# ============================================================
# W5: rename 子命令 (真实集成)
# ============================================================

_RENAME_STATUS_ZH = {
    "OK": "成功",
    "RENAMED": "改名",
    "OVERWRITTEN": "覆盖",
    "CONFLICT": "冲突",
    "SKIPPED": "跳过",
    "DRY_RUN": "试运行",
    "ERROR": "失败",
}


def _make_progress_bar(pct: float, width: int = 30) -> str:
    """ASCII 进度条: [████░░░░] 50%"""
    filled = int(pct / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}]"


def _cmd_rename(args: argparse.Namespace) -> int:
    """W5: rename 真实集成.

    流程: 扫描源目录 → 构造 Template + Renamer → apply_with_progress →
    按文件逐个回调 on_progress 打进度 → 汇总统计.
    """
    from filemaster.core.renamer import ConflictStrategy, Renamer
    from filemaster.core.template import Template
    from filemaster.core.undo import UndoStack

    source = Path(args.source).resolve()
    if not source.exists():
        print(f"❌ 源路径不存在: {source}", file=sys.stderr)
        return 1

    # 1) 解析模板
    try:
        tpl = Template(args.template)
    except ValueError as e:
        print(f"❌ 模板无效: {e}", file=sys.stderr)
        return 1

    # 2) 冲突策略
    try:
        conflict_strategy = ConflictStrategy(args.conflict)
    except ValueError:
        valid = ", ".join(s.value for s in ConflictStrategy)
        print(f"❌ 冲突策略无效: {args.conflict} (合法: {valid})", file=sys.stderr)
        return 1

    # 3) 扫描文件: -s 可为单文件或目录 (W5 增强: 单文件模式避免 dir scan
    #    把冲突目标也一起捞进来, 让 collision 测试可精确定位)
    if source.is_file():
        files = [source]
    elif args.recursive:
        files = sorted(p for p in source.rglob("*") if p.is_file())
    else:
        files = sorted(p for p in source.iterdir() if p.is_file())
    if not files:
        print(f"⚠️  目录无文件: {source}")
        return 0

    # 4) 构造 Renamer + UndoStack
    renamer = Renamer(tpl, prefix=args.prefix, start_index=args.start_index)
    undo_stack: UndoStack | None = None
    if not args.dry_run:
        undo_stack = UndoStack()

    # JSON 模式不打 header (避免污染 stdout 让 json.loads 直接解析)
    if not args.json:
        mode_label = "🔍 DRY-RUN" if args.dry_run else "🚀 EXEC"
        print(
            f"{mode_label}  rename {len(files)} 文件 / "
            f"模板={tpl.raw!r} 前缀={args.prefix!r} "
            f"冲突={conflict_strategy.value} 起始序号={args.start_index}"
        )

    # 5) 进度回调 + 异步执行 (W5: 用 threading 让进度回调实时刷新,
    #    apply_with_progress 内部仍按 1-by-1 串行跑, 保证 self._index 顺序)
    progress_done = {"count": 0, "last_msg": ""}

    def on_progress(i: int, total: int, file: Path, result) -> None:
        progress_done["count"] = i
        pct = i / total * 100
        # JSON 模式不打进度条 (避免污染 stdout)
        if args.json:
            return
        bar = _make_progress_bar(pct)
        target_name = result.target.name if result.target else "(skip)"
        status_zh = _RENAME_STATUS_ZH.get(result.status, result.status)
        line = f"  {bar} {pct:5.1f}% ({i}/{total})  {file.name} → {target_name}  [{status_zh}]   "
        # 截断太长 (避免 progress 乱跳)
        if len(line) > 120:
            line = line[:117] + "..."
        print(f"\r{line}", end="", flush=True)
        if i == total:
            print()  # 结束换行

    # 异步包装 — dry-run 走 plan (不动文件), 真执行走 apply_with_progress.
    # 真正"并发"留给 W6 (ThreadPoolExecutor 分片), W5 仍串行保证 Index 正确.
    result_holder: dict = {"results": None, "exc": None}

    def _worker() -> None:
        try:
            if args.dry_run:
                # plan 只生成 RenameResult, 不动文件 (status="DRY_RUN")
                plan_results = renamer.plan(files)
                # 把 plan 结果也通过 on_progress 走一遍, 让进度条统一
                for i, r in enumerate(plan_results, 1):
                    if on_progress is not None:
                        on_progress(i, len(plan_results), r.source, r)
                result_holder["results"] = plan_results
            else:
                result_holder["results"] = renamer.apply_with_progress(
                    files, conflict_strategy, undo_stack, on_progress
                )
        except Exception as e:
            result_holder["exc"] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()

    if result_holder["exc"] is not None:
        print(f"\n❌ 重命名失败: {result_holder['exc']}", file=sys.stderr)
        return 1

    results = result_holder["results"]
    if results is None:
        print("❌ 重命名未产生结果", file=sys.stderr)
        return 1

    # 6) 汇总统计
    stats = {
        "total": len(results),
        "ok": sum(1 for r in results if r.status in ("OK", "RENAMED", "OVERWRITTEN")),
        "conflict": sum(1 for r in results if r.status == "CONFLICT"),
        "skipped": sum(1 for r in results if r.status == "SKIPPED"),
        "dry_run": sum(1 for r in results if r.status == "DRY_RUN"),
        "error": sum(1 for r in results if r.status == "ERROR"),
    }

    # 7) 输出
    if args.json:
        payload = {
            "mode": "dry-run" if args.dry_run else "exec",
            "template": tpl.raw,
            "prefix": args.prefix,
            "conflict_strategy": conflict_strategy.value,
            "stats": stats,
            "items": [
                {
                    "source": str(r.source),
                    "target": str(r.target) if r.target else None,
                    "status": r.status,
                    "message": r.message,
                }
                for r in results
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"\n📊 完成: 总数={stats['total']} "
            f"成功={stats['ok']} 冲突跳过={stats['conflict']} "
            f"跳过={stats['skipped']} 试运行={stats['dry_run']} 失败={stats['error']}"
        )
        if not args.dry_run and stats["ok"] > 0:
            print(f"   ↩ undo:  撤销栈已记录 ({stats['ok']} 个 entry), 可用 UndoStack 恢复")
        if stats["error"] > 0:
            print("\n   失败明细:")
            for r in results:
                if r.status == "ERROR":
                    print(f"     ✗ {r.source.name}: {r.message}")

    return 0 if stats["error"] == 0 else 1


# ============================================================
# W10: archive — 压缩归档 (zip / tar.gz / tar.bz2)
# ============================================================


_ARCHIVE_STATUS_ZH = {"OK": "完成", "CANCELLED": "取消", "ERROR": "错误"}


"""W10: archive — 压缩归档 (zip / tar.gz / tar.bz2)

两种模式:
  1) 单卷: -s <dir|file> -o <out_dir> -n <name>.[zip|tar.gz|tar.bz2]
  2) 按类分卷: -s <dir> --by-category (生成 IMAGE.zip / DOCUMENT.zip / ...)
走 Archiver.archive_with_progress (W7 协作式 + W9 硬中断).
"""


def _cmd_archive(args: argparse.Namespace) -> int:
    from filemaster.core.archiver import ArchiveFormat, Archiver

    source = Path(args.source).resolve()
    output_dir = Path(args.output).resolve()
    fmt = ArchiveFormat(args.format)

    if not source.exists():
        print(f"❌ 源路径不存在: {source}", file=sys.stderr)
        return 1

    # 1) 收集文件
    if source.is_file():
        files = [source]
    elif args.recursive:
        files = sorted(p for p in source.rglob("*") if p.is_file())
    else:
        files = sorted(p for p in source.iterdir() if p.is_file())
    if not files:
        print(f"⚠️  源无文件: {source}")
        return 0

    # 2) 试运行
    if args.dry_run:
        if args.json:
            print(json.dumps({
                "mode": "by_category" if args.by_category else "single",
                "format": fmt.value,
                "compression": args.compression,
                "source": str(source),
                "output": str(output_dir),
                "files": [str(f) for f in files],
                "count": len(files),
            }, ensure_ascii=False, indent=2))
        else:
            mode = "按类分卷" if args.by_category else "单卷"
            print(f"🔍 DRY-RUN  archive {mode} 格式={fmt.value} 压缩={args.compression}")
            print(f"   源: {source}")
            print(f"   输出: {output_dir}")
            print(f"   文件数: {len(files)}")
        return 0

    # 3) 进度回调 + 异步
    progress_done = {"count": 0, "last_msg": ""}

    def on_progress(i: int, total: int, file: Path, written: int) -> None:
        progress_done["count"] = i
        if args.json:
            return
        pct = i / total * 100
        bar = _make_progress_bar(pct)
        line = f"  {bar} {pct:5.1f}% ({i}/{total})  {file.name}   "
        if len(line) > 120:
            line = line[:117] + "..."
        print(f"\r{line}", end="", flush=True)
        if i == total:
            print()

    result_holder: dict = {"results": None, "exc": None}

    def _worker() -> None:
        try:
            archiver = Archiver()
            if args.by_category:
                cat_results = archiver.archive_by_category(
                    files, output_dir, fmt=fmt, compression=args.compression,
                    on_progress=on_progress,
                )
                result_holder["results"] = list(cat_results.values())
            else:
                archive_path = output_dir / f"{args.name}{fmt.extension}"
                result_holder["results"] = [archiver.archive_with_progress(
                    files, archive_path, fmt=fmt, compression=args.compression,
                    on_progress=on_progress,
                )]
        except Exception as e:
            result_holder["exc"] = e

    # W5 模式: 简单 threading 包装 (UI / 实时刷新进度)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()

    if result_holder["exc"] is not None:
        print(f"❌ 归档失败: {result_holder['exc']}", file=sys.stderr)
        return 1
    results = result_holder["results"] or []

    # 4) 汇总
    if args.json:
        out = [{
            "archive_path": str(r.archive_path),
            "source_count": r.source_count,
            "written_bytes": r.written_bytes,
            "elapsed": round(r.elapsed, 3),
            "status": r.status,
            "message": r.message,
        } for r in results]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"\n✅ 归档完成 — {len(results)} 卷")
        for r in results:
            label = _ARCHIVE_STATUS_ZH.get(r.status, r.status)
            print(
                f"  [{label}] {r.archive_path.name}  "
                f"{r.source_count} 文件 / {r.written_bytes:,} B / {r.elapsed:.2f}s"
            )

    return 0 if all(r.status == "OK" for r in results) else 1


_ARCHIVE_STATUS_ZH = {
    "OK": "完成",
    "CANCELLED": "取消",
    "ERROR": "错误",
}


# ============================================================
# W4 v4: dedup-scan + dedup-move/delete/hardlink
# ============================================================


def _cmd_dedup_scan(args: argparse.Namespace) -> int:
    """扫描 + 找重复 (只查, 不动文件)."""
    from filemaster.core.dedup import Deduper

    source = Path(args.source).resolve()
    if not source.is_dir():
        print(f"❌ 源目录不存在: {source}", file=sys.stderr)
        return 1

    print(f"🔍 扫描 {source} (算法={args.algorithm}, 递归={args.recursive}) ...")
    deduper = Deduper(algorithm=args.algorithm)
    if args.recursive:
        files = sorted(p for p in source.rglob("*") if p.is_file())
    else:
        files = sorted(p for p in source.iterdir() if p.is_file())
    groups, stats = deduper.find_duplicates_with_meta(files)

    print(
        f"📊 扫描 {stats.total_files} 个文件 / "
        f"发现 {stats.duplicate_groups} 组重复 / "
        f"{stats.duplicate_files} 个可清理 / "
        f"浪费 {stats.wasted_human} / 耗时 {stats.duration_ms} ms"
    )

    if args.json:
        # JSON 模式: 输出可脚本消费的格式
        out = {
            "stats": stats.to_dict(),
            "groups": [
                {
                    "hash": g.hash_value,
                    "algorithm": g.algorithm,
                    "size": g.hash_size,
                    "count": g.count,
                    "wasted_bytes": g.wasted_bytes,
                    "keeper": str(g.keeper),
                    "duplicates": [str(f) for f in g.duplicates],
                }
                for g in groups
            ],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        # 人类可读模式
        for i, g in enumerate(groups, 1):
            keeper = g.keeper
            print(
                f"\n  [{i}] hash={g.hash_value[:12]}... "
                f"size={g.hash_size}B count={g.count} "
                f"wasted={g.wasted_bytes}B"
            )
            print(f"      keeper:    {keeper}")
            for dup in g.duplicates:
                print(f"      duplicate: {dup}")

    return 0


def _cmd_dedup_action(action: str) -> callable:
    """构造 dedup-move / dedup-delete / dedup-hardlink 共用 handler."""
    from filemaster.core.dedup import (
        Deduper,
        delete_duplicates,
        hardlink_duplicates,
        move_duplicates,
    )

    func_map = {
        "move": move_duplicates,
        "delete": delete_duplicates,
        "hardlink": hardlink_duplicates,
    }

    def handler(args: argparse.Namespace) -> int:
        source = Path(args.source).resolve()
        if not source.is_dir():
            print(f"❌ 源目录不存在: {source}", file=sys.stderr)
            return 1

        # 1) 扫描
        print(f"🔍 扫描 {source} (算法={args.algorithm}, 递归={args.recursive}) ...")
        deduper = Deduper(algorithm=args.algorithm)
        if args.recursive:
            files = sorted(p for p in source.rglob("*") if p.is_file())
        else:
            files = sorted(p for p in source.iterdir() if p.is_file())
        groups, stats = deduper.find_duplicates_with_meta(files)
        if stats.duplicate_groups == 0:
            print("📊 未发现重复文件, 退出")
            return 0
        print(
            f"📊 {stats.duplicate_groups} 组 / "
            f"{stats.duplicate_files} 个可处理 / "
            f"浪费 {stats.wasted_human}"
        )

        # 2) 目标目录(只 move 用)
        target_dir = None
        if action == "move" and args.target:
            target_dir = Path(args.target).resolve()
            print(f"📂 目标目录: {target_dir}")

        # 3) 跑动作
        func = func_map[action]
        total_ok = 0
        total_fail = 0
        for i, g in enumerate(groups, 1):
            kw = {"dry_run": args.dry_run}
            if action == "move":
                kw["target_dir"] = target_dir
                kw["overwrite"] = args.overwrite
            elif action == "delete":
                kw["use_trash"] = args.use_trash
            elif action == "hardlink":
                kw["overwrite"] = args.overwrite

            batch = func(g, **kw)
            mode = "DRY-RUN" if args.dry_run else "EXEC"
            print(
                f"\n  [{i}/{len(groups)}] {mode} {action}: "
                f"成功 {batch.success_count}/{len(g.duplicates)} "
                f"失败 {batch.fail_count}"
            )
            for r in batch.results:
                if not r.success:
                    print(f"    ✗ {r.source}: {r.error}")
            total_ok += batch.success_count
            total_fail += batch.fail_count
            if batch.undo_log_path:
                print(f"    ↩ undo log: {batch.undo_log_path}")

        print(
            f"\n✅ 完成: 成功 {total_ok} / 失败 {total_fail} / "
            f"{'dry-run' if args.dry_run else '实际'}"
        )
        return 0 if total_fail == 0 else 1

    return handler


# ============================================================
# 顶层 parser
# ============================================================


def _cmd_dedup_undo_list(args: argparse.Namespace) -> int:
    """W4 v5: 列出所有 undo log (按时间倒序)."""
    from filemaster.core.dedup import list_undo_logs

    logs = list_undo_logs()
    if not logs:
        print("📂 没有 undo log (默认在 ~/.filemaster/undo/)")
        return 0
    print(f"📂 找到 {len(logs)} 个 undo log:\n")
    for log in logs:
        flag = "✓ 可恢复" if log.can_restore else "✗ 不可恢复"
        print(f"  {log.path.name}  {flag}")
        print(f"    action={log.action}  timestamp={log.timestamp}")
        print(f"    keeper={log.keeper}")
        print(f"    entries={log.entry_count}")
        print()
    return 0


def _cmd_dedup_undo_restore(args: argparse.Namespace) -> int:
    """W4 v5: 从 undo log 恢复文件 (反向 move)."""
    from filemaster.core.dedup import restore_undo_log

    log_path = Path(args.log)
    try:
        results = restore_undo_log(
            log_path,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return 1
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    mode = "DRY-RUN" if args.dry_run else "EXEC"
    success = sum(1 for r in results if r.success)
    skipped = sum(1 for r in results if r.skipped)
    failed = sum(1 for r in results if not r.success and not r.skipped)
    print(f"🔄 恢复完成 [{mode}]: 成功 {success} / 跳过 {skipped} / 失败 {failed}\n")
    for r in results:
        if r.success:
            tag = "[DRY]" if args.dry_run else "✓"
            print(f"  {tag} {r.source} → {r.target}")
        elif r.skipped:
            print(f"  ⏭️  跳过 {r.source} → {r.target} ({r.error})")
        else:
            print(f"  ✗ {r.source} → {r.target}: {r.error}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器."""
    parser = argparse.ArgumentParser(
        prog="filemaster",
        description="FileMaster — 文件批量处理工具（W5: rename 集成）",
    )
    parser.add_argument("--version", action="version", version="0.5.0")

    sub = parser.add_subparsers(dest="command", help="子命令")

    # ----- classify -----
    p_classify = sub.add_parser(
        "classify", help="按类型分类（11 类 + magic bytes 检测）"
    )
    p_classify.add_argument("-s", "--source", required=True, help="源目录")
    p_classify.add_argument(
        "-r", "--recursive", action="store_true", help="递归子目录"
    )
    p_classify.add_argument(
        "--json", action="store_true", help="输出 JSON（便于脚本管道）"
    )
    p_classify.add_argument(
        "--group", action="store_true", help="按类别分组展示"
    )
    p_classify.add_argument(
        "--by-category",
        action="store_true",
        help="分组展示 + 列出每个类别下文件",
    )
    p_classify.add_argument(
        "--copy", metavar="DEST", help="按类别复制到 DEST/<Category>/"
    )
    p_classify.add_argument(
        "--move", metavar="DEST", help="按类别移动到 DEST/<Category>/"
    )
    p_classify.add_argument(
        "--dry-run", action="store_true", help="试运行（--copy/--move 时）"
    )
    p_classify.set_defaults(func=_cmd_classify)

    # ----- rename (W5 真实集成) -----
    p_rename = sub.add_parser("rename", help="批量重命名（W5: 真实集成）")
    p_rename.add_argument("-s", "--source", required=True, help="源目录")
    p_rename.add_argument(
        "-t", "--template",
        default="{Prefix}{Index:D3}_{OriginalName}",
        help="命名模板 (默认 {Prefix}{Index:D3}_{OriginalName})",
    )
    p_rename.add_argument("-p", "--prefix", default="", help="前缀")
    p_rename.add_argument(
        "--start-index", type=int, default=1, help="起始序号 (默认 1)"
    )
    p_rename.add_argument(
        "-r", "--recursive", action="store_true", help="递归子目录"
    )
    p_rename.add_argument(
        "--conflict",
        default="skip",
        choices=["skip", "overwrite", "rename_new"],
        help="冲突策略 (默认 skip)",
    )
    p_rename.add_argument(
        "--dry-run", action="store_true", help="试运行 (不真改, 只列计划)"
    )
    p_rename.add_argument(
        "--json", action="store_true", help="输出 JSON (便于脚本管道)"
    )
    p_rename.set_defaults(func=_cmd_rename)

    # ----- archive (W10) -----
    p_archive = sub.add_parser(
        "archive", help="压缩归档 (W10: zip / tar.gz / tar.bz2)"
    )
    p_archive.add_argument(
        "-s", "--source", required=True, help="源目录 (--by-category 模式) 或单文件"
    )
    p_archive.add_argument(
        "-o", "--output", required=True, help="归档输出目录"
    )
    p_archive.add_argument(
        "-n", "--name", default="archive",
        help="归档名 (单卷模式, 默认 archive, 扩展名按 --format 自动加)",
    )
    p_archive.add_argument(
        "--format", default="zip", choices=["zip", "tar.gz", "tar.bz2"],
        help="归档格式 (默认 zip)",
    )
    p_archive.add_argument(
        "--compression", type=int, default=6, choices=range(0, 10),
        help="压缩级别 0-9 (默认 6, 0 = 不压缩, 9 = 最大)",
    )
    p_archive.add_argument(
        "--by-category", action="store_true",
        help="按内置分类分卷 (IMAGE/DOCUMENT/AUDIO/VIDEO/...)",
    )
    p_archive.add_argument(
        "-r", "--recursive", action="store_true", help="递归子目录 (--by-category 必加)"
    )
    p_archive.add_argument(
        "--dry-run", action="store_true", help="试运行 (不真写, 只列计划)"
    )
    p_archive.add_argument(
        "--json", action="store_true", help="输出 JSON (便于脚本管道)"
    )
    p_archive.set_defaults(func=_cmd_archive)

    # ----- dedup-scan (W4 v4) -----
    p_dedup_scan = sub.add_parser(
        "dedup-scan", help="扫描找重复（W4 v4：只查, 不动文件）"
    )
    p_dedup_scan.add_argument("-s", "--source", required=True, help="源目录")
    p_dedup_scan.add_argument(
        "-r", "--recursive", action="store_true", default=True, help="递归（默认开）"
    )
    p_dedup_scan.add_argument(
        "--algorithm", default="md5",
        choices=["md5", "sha1", "sha256", "blake2b"],
        help="hash 算法（默认 md5）",
    )
    p_dedup_scan.add_argument(
        "--json", action="store_true", help="输出 JSON 格式（便于脚本管道）"
    )
    p_dedup_scan.set_defaults(func=_cmd_dedup_scan)

    # ----- dedup-move (W4 v4) -----
    p_dedup_move = sub.add_parser(
        "dedup-move", help="把重复文件移到目标目录（W4 v4）"
    )
    p_dedup_move.add_argument("-s", "--source", required=True, help="源目录")
    p_dedup_move.add_argument(
        "-t", "--target", help="目标目录（默认 <source>/_duplicates/）"
    )
    p_dedup_move.add_argument(
        "-r", "--recursive", action="store_true", default=True, help="递归（默认开）"
    )
    p_dedup_move.add_argument(
        "--algorithm", default="md5", choices=["md5", "sha1", "sha256", "blake2b"]
    )
    p_dedup_move.add_argument(
        "--overwrite", action="store_true", help="覆盖已存在的目标"
    )
    p_dedup_move.add_argument(
        "--dry-run", action="store_true", help="只列将要做什么, 不真动"
    )
    p_dedup_move.set_defaults(func=_cmd_dedup_action("move"))

    # ----- dedup-delete (W4 v4) -----
    p_dedup_delete = sub.add_parser(
        "dedup-delete", help="删重复文件（W4 v4）"
    )
    p_dedup_delete.add_argument("-s", "--source", required=True, help="源目录")
    p_dedup_delete.add_argument(
        "-r", "--recursive", action="store_true", default=True, help="递归（默认开）"
    )
    p_dedup_delete.add_argument(
        "--algorithm", default="md5", choices=["md5", "sha1", "sha256", "blake2b"]
    )
    p_dedup_delete.add_argument(
        "--use-trash", action="store_true", default=True,
        help="移到回收站 (默认开, 需要 send2trash 库)",
    )
    p_dedup_delete.add_argument(
        "--no-trash", dest="use_trash", action="store_false",
        help="直接删, 不进回收站 (危险)",
    )
    p_dedup_delete.add_argument(
        "--dry-run", action="store_true", help="只列将要做什么, 不真删"
    )
    p_dedup_delete.set_defaults(func=_cmd_dedup_action("delete"))

    # ----- dedup-hardlink (W4 v4) -----
    p_dedup_hl = sub.add_parser(
        "dedup-hardlink", help="用硬链替换重复文件指向 keeper (W4 v4)"
    )
    p_dedup_hl.add_argument("-s", "--source", required=True, help="源目录")
    p_dedup_hl.add_argument(
        "-r", "--recursive", action="store_true", default=True, help="递归（默认开）"
    )
    p_dedup_hl.add_argument(
        "--algorithm", default="md5", choices=["md5", "sha1", "sha256", "blake2b"]
    )
    p_dedup_hl.add_argument(
        "--dry-run", action="store_true", help="只列将要做什么, 不真改"
    )
    p_dedup_hl.set_defaults(func=_cmd_dedup_action("hardlink"))

    # ----- dedup-undo (W4 v5) -----
    p_dedup_undo = sub.add_parser(
        "dedup-undo", help="从 undo log 恢复 move 操作 (W4 v5)"
    )
    undo_sub = p_dedup_undo.add_subparsers(
        dest="undo_action", required=True
    )
    p_undo_list = undo_sub.add_parser(
        "list", help="列出所有 undo log"
    )
    p_undo_list.set_defaults(func=_cmd_dedup_undo_list)
    p_undo_restore = undo_sub.add_parser(
        "restore", help="从指定 undo log 恢复"
    )
    p_undo_restore.add_argument(
        "log", help="undo log JSON 文件路径 (用 `dedup-undo list` 看)"
    )
    p_undo_restore.add_argument(
        "--overwrite", action="store_true",
        help="目标已存在时强制覆盖 (默认跳过)"
    )
    p_undo_restore.add_argument(
        "--dry-run", action="store_true", help="只列将要做什么, 不真改"
    )
    p_undo_restore.set_defaults(func=_cmd_dedup_undo_restore)

    return parser


def main() -> int:
    """CLI 主入口."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
