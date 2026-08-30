"""GUI 应用入口."""

from __future__ import annotations

import sys


def main() -> int:
    """启动 FileMaster GUI."""
    from PySide6.QtWidgets import QApplication

    from filemaster.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FileMaster")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("ECAS")

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
