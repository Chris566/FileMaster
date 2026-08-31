"""命令行接口.

W4 v1 实现：
- classify 子命令：用 core/classifier.py 真实分类
  - --json：输出 JSON 给脚本管道
  - --copy/--move：按类别复制/移动到子目录
  - --group：按类别分组展示
  - --by-category：按类别目录汇总
- rename 子命令：占位（W2 引擎已就绪，但 CLI 留 W5 集成）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
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
# rename 子命令（占位，留 W5 集成）
# ============================================================


def _cmd_rename(args: argparse.Namespace) -> int:
    """重命名子命令（W5 落地，目前占位）."""
    print("[W5] rename 命令已解析，引擎在 filemaster.core.renamer，CLI 留 W5 集成")
    print(f"  参数：{vars(args)}")
    return 0


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
        description="FileMaster — 文件批量处理工具（W4 v4：Dedup 动作）",
    )
    parser.add_argument("--version", action="version", version="0.4.0")

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

    # ----- rename (placeholder) -----
    p_rename = sub.add_parser("rename", help="批量重命名（W5 集成）")
    p_rename.add_argument("-s", "--source", required=True, help="源目录")
    p_rename.add_argument("-p", "--prefix", default="", help="前缀")
    p_rename.add_argument(
        "-t", "--template",
        default="{Prefix}{OriginalName}",
        help="命名模板",
    )
    p_rename.add_argument("--dry-run", action="store_true", help="试运行")
    p_rename.set_defaults(func=_cmd_rename)

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
