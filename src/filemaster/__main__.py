"""FileMaster 入口点.

支持两种启动方式：
- `python -m filemaster`        # 默认启动 GUI
- `python -m filemaster --cli`  # 启动 CLI
"""

import sys


def main() -> int:
    """主入口."""
    if "--cli" in sys.argv or "-c" in sys.argv:
        # 移除 --cli，避免污染 argparse
        if "--cli" in sys.argv:
            sys.argv.remove("--cli")
        if "-c" in sys.argv:
            sys.argv.remove("-c")
        from filemaster.cli import main as cli_main

        return cli_main()

    from filemaster.app import main as app_main

    return app_main()


if __name__ == "__main__":
    sys.exit(main())
