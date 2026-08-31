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
# 8/30 立过 smoke 脚本的同款问题（MEMORY.md），这次漏了 CLI 子进程入口。
# 在模块加载时 reconfigure 到 UTF-8，errors="replace" 兜底任何真编码不了的字符。
# ============================================================
def _ensure_utf8_io() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # Python < 3.7 不支持
        try:
            reconfigure(encoding="utf-8", errors="replace")
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
# 顶层 parser
# ============================================================


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器."""
    parser = argparse.ArgumentParser(
        prog="filemaster",
        description="FileMaster — 文件批量处理工具（W4 v1：Classifier）",
    )
    parser.add_argument("--version", action="version", version="0.3.0")

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
