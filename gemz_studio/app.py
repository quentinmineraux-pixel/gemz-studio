from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .config import APP_NAME, resource_path
from .ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    icon = resource_path("assets/icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    window.show()
    return app.exec()
