"""FileMaster 主题截图脚本.

在 offscreen Qt 平台下渲染 MainWindow 的 light / dark / fluent / high_contrast 主题，导出 PNG。

用法：
    QT_QPA_PLATFORM=offscreen python scripts/screenshot_themes.py

产物：
    artifacts/screenshots/{light,dark,fluent,high_contrast}.png
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows 默认 stdout 是 cp936，不能编码中文 print
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# 把 src 加进 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from filemaster.ui.main_window import MainWindow


def main() -> int:
    """渲染 4 套主题并截图."""
    # 输出在 repo 内 <repo>/artifacts/screenshots（actions/upload-artifact 不允许 .. 模式）
    artifacts = Path(__file__).resolve().parent.parent / "artifacts"
    out_dir = artifacts / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("FileMaster 主题截图渲染（offscreen Qt 平台）")
    print("=" * 60)

    app = QApplication(sys.argv)
    app.setApplicationName("FileMaster")

    themes = [
        ("light", "浅色 (Fluent Light)"),
        ("dark", "深色 (Fluent Dark)"),
        ("fluent", "Fluent 亚克力"),
        ("high_contrast", "高对比度 (WCAG AAA)"),
    ]

    for theme_key, _ in themes:
        # 每次创建独立 MainWindow 实例，避免主题状态污染
        win = MainWindow()
        win.resize(1280, 800)

        # 强制指定主题（绕过默认 light）
        win._apply_theme(theme_key)
        win.show()

        # 给 Qt 几次事件循环让 layout 完成
        for _ in range(3):
            app.processEvents()

        # 截屏
        pixmap: QPixmap = win.grab()
        out_path = out_dir / f"{theme_key}.png"
        success = pixmap.save(str(out_path), "PNG")
        size = out_path.stat().st_size if out_path.exists() else 0
        print(f"  {theme_key:<16} -> {out_path.name:<22} {pixmap.width()}x{pixmap.height()}  {size:>6} bytes  {'OK' if success else 'FAIL'}")

        win.close()
        # 清理所有 widget 状态
        app.processEvents()

    print()
    print(f"截图产物在 {out_dir} 下，4 套主题齐全。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
