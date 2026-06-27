"""
Know Your Projects — Main Entry Point
"""
import sys
import os

# Ensure the project root is in the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon
from ui.main_window import MainWindow


def main():
    # Enable high-DPI scaling
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    app = QApplication(sys.argv)
    app.setApplicationName("Know Your Projects")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("KYP")

    # Set default font
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    # Load and apply dark theme by default
    theme_path = os.path.join(os.path.dirname(__file__), "assets", "styles", "dark_theme.qss")
    if os.path.exists(theme_path):
        with open(theme_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
