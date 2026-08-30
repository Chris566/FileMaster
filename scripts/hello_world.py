"""FileMaster Hello World 演示.

跑通 PySide6 + 4 主题 + 模板渲染 + 简单交互。

用法：
    python scripts/hello_world.py

按工具栏按钮可在 4 套主题间切换，左侧"开始"按钮会演示模板渲染（不写文件）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 把 src 加进 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtWidgets import QApplication

from filemaster.ui.main_window import MainWindow


def main() -> int:
    """启动 hello world."""
    print("=" * 60)
    print("FileMaster v0.1.0 (W1) — Hello World")
    print("=" * 60)
    print()
    print("✓ 工程脚手架: pyproject.toml + 目录结构 + CI")
    print("✓ 核心接口: Template / Renamer / Classifier / Undo / Hash / Config")
    print("✓ 4 套 QSS 主题: light / dark / fluent / high_contrast")
    print("✓ 单元测试: 35+ 个用例")
    print()
    print("功能测试：")
    print("  - 工具栏 '主题' 下拉切换 4 套主题")
    print("  - 左侧 '▶ 开始' 触发模板渲染（不写文件）")
    print("  - 菜单 '视图 > 主题' 同样可切换")
    print("  - 'Ctrl+Q' 退出")
    print()

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FileMaster Hello World")

    window = MainWindow()
    window.setWindowTitle("FileMaster Hello World (W1)")
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
