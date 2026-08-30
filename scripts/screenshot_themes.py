"""FileMaster 主题截图脚本.

在 offscreen Qt 平台下渲染 MainWindow 的 light / dark 两套主题，导出 PNG 用于交付预览。

用法：
    QT_QPA_PLATFORM=offscreen python scripts/screenshot_themes.py

产物：
    artifacts/screenshots/light.png
    artifacts/screenshots/dark.png
"""

from __future__ import annotations

import sys
from pathlib import Path

# 把 src 加进 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QPushButton, QListWidget, QTextEdit, QWidget, QVBoxLayout, QHBoxLayout, QToolBar, QStatusBar

from filemaster.ui.main_window import MainWindow


def main() -> int:
    """渲染 4 套主题并截图."""
    artifacts = Path(__file__).resolve().parent.parent.parent / "artifacts"
    out_dir = artifacts / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("FileMaster 主题截图渲染（offscreen Qt 平台）")
    print("=" * 60)

    # 应用样式表
    styles_dir = Path(__file__).resolve().parent.parent / "src" / "filemaster" / "ui" / "styles"

    themes = [
        ("light", "theme_light.qss"),
        ("dark", "theme_dark.qss"),
        ("fluent", "theme_fluent.qss"),
        ("high_contrast", "theme_high_contrast.qss"),
    ]

    app = QApplication(sys.argv)
    app.setApplicationName("FileMaster")

    for theme_name, qss_file in themes:
        qss_path = styles_dir / qss_file
        qss_content = qss_path.read_text(encoding="utf-8")
        app.setStyleSheet(qss_content)

        # 创建主窗口
        win = MainWindow()
        win.resize(1280, 800)
        win.show()

        # 触发一次 layout 完成
        app.processEvents()

        # 截屏
        pixmap: QPixmap = win.grab()
        out_path = out_dir / f"{theme_name}.png"
        success = pixmap.save(str(out_path), "PNG")
        size = out_path.stat().st_size if out_path.exists() else 0
        print(f"  {theme_name:<16} -> {out_path.name:<22} {pixmap.width()}x{pixmap.height()}  {size:>6} bytes  {'OK' if success else 'FAIL'}")

        win.close()

    print()
    print(f"截图产物在 {out_dir} 下，4 套主题齐全。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
