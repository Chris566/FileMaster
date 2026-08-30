"""命令行接口（占位 W11 实现）.

当前仅占位，W1 只做最小可用版本。完整 CLI 在 W11 落地。
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器."""
    parser = argparse.ArgumentParser(
        prog="filemaster",
        description="FileMaster — 文件批量处理工具",
    )
    parser.add_argument("--version", action="version", version="0.1.0")

    sub = parser.add_subparsers(dest="command", help="子命令")

    # rename
    p_rename = sub.add_parser("rename", help="批量重命名")
    p_rename.add_argument("-s", "--source", required=True, help="源目录")
    p_rename.add_argument("-p", "--prefix", default="", help="前缀")
    p_rename.add_argument("-t", "--template", default="{Prefix}{OriginalName}",
                          help="命名模板")
    p_rename.add_argument("--dry-run", action="store_true", help="试运行")

    # classify
    p_classify = sub.add_parser("classify", help="按类型分类复制")
    p_classify.add_argument("-s", "--source", required=True)
    p_classify.add_argument("-d", "--destination", required=True)
    p_classify.add_argument("-r", "--recursive", action="store_true")

    return parser


def main() -> int:
    """CLI 主入口."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    print(f"[W1] 命令 {args.command} 已解析，但业务逻辑待 W2-W4 落地")
    print(f"  参数：{vars(args)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
