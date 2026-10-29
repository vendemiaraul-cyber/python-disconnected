"""Qt user interface."""

from __future__ import annotations

import sys
from pathlib import Path


def run(argv: list[str] | None = None, config=None) -> int:
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("MiniTok")
    apply_theme(app)
    window = MainWindow(config)
    window.show()
    return app.exec()


def apply_theme(app) -> None:
    """Fusion + a dark palette so native widgets match the stylesheet on every OS."""
    from PySide6.QtGui import QColor, QPalette

    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in {
        QPalette.Window: "#121212", QPalette.WindowText: "#f0f0f0", QPalette.Base: "#1c1c1c",
        QPalette.AlternateBase: "#232323", QPalette.Text: "#f0f0f0", QPalette.Button: "#2b2b2b",
        QPalette.ButtonText: "#ffffff", QPalette.Highlight: "#fe2c55", QPalette.HighlightedText: "#ffffff",
        QPalette.ToolTipBase: "#222222", QPalette.ToolTipText: "#ffffff", QPalette.PlaceholderText: "#777777",
    }.items():
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet((Path(__file__).with_name("style.qss")).read_text(encoding="utf-8"))
