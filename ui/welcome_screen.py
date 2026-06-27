"""
welcome_screen.py — The startup/welcome screen for Know Your Projects.
Shown when no project is loaded. Features drag-and-drop and GitHub URL input.
"""
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QSizePolicy, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QSize
from PyQt6.QtGui import QFont, QDragEnterEvent, QDropEvent, QColor, QPalette, QLinearGradient


class WelcomeScreen(QWidget):
    """
    Landing screen with:
    - Animated gradient background
    - Drag-and-drop zone for folders
    - GitHub URL input
    - Recent projects list
    """

    open_folder_requested = pyqtSignal(str)
    open_github_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(0)
        layout.setContentsMargins(40, 40, 40, 40)

        # ── Hero Section ─────────────────────────────────────────────────
        hero = QFrame()
        hero.setObjectName("heroSection")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.setSpacing(12)

        # Logo / Icon
        icon_label = QLabel("🧠")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFont(QFont("Segoe UI Emoji", 56))
        hero_layout.addWidget(icon_label)

        # App Name
        app_name = QLabel("Know Your Projects")
        app_name.setObjectName("appName")
        app_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app_name.setFont(QFont("Segoe UI", 32, QFont.Weight.Bold))
        hero_layout.addWidget(app_name)

        # Tagline
        tagline = QLabel("Understand any codebase instantly — powered by AI")
        tagline.setObjectName("tagline")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setFont(QFont("Segoe UI", 13))
        tagline.setStyleSheet("color: #8b949e; margin-bottom: 8px;")
        hero_layout.addWidget(tagline)

        layout.addWidget(hero)
        layout.addSpacing(32)

        # ── Drop Zone ────────────────────────────────────────────────────
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("dropZone")
        self.drop_zone.setFixedSize(500, 180)
        self.drop_zone.setCursor(Qt.CursorShape.PointingHandCursor)
        drop_layout = QVBoxLayout(self.drop_zone)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.setSpacing(10)

        drop_icon = QLabel("📂")
        drop_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_icon.setFont(QFont("Segoe UI Emoji", 32))
        drop_layout.addWidget(drop_icon)

        drop_title = QLabel("Drop a project folder here")
        drop_title.setObjectName("dropTitle")
        drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        drop_layout.addWidget(drop_title)

        drop_sub = QLabel("or click to browse")
        drop_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_sub.setStyleSheet("color: #8b949e; font-size: 11px;")
        drop_layout.addWidget(drop_sub)

        self.drop_zone.mousePressEvent = self._browse_folder
        layout.addWidget(self.drop_zone, alignment=Qt.AlignmentFlag.AlignCenter)

        # ── Divider ──────────────────────────────────────────────────────
        layout.addSpacing(20)
        divider_row = QHBoxLayout()
        left_line = QFrame()
        left_line.setFrameShape(QFrame.Shape.HLine)
        left_line.setStyleSheet("color: #30363d;")
        or_label = QLabel("  or  ")
        or_label.setStyleSheet("color: #484f58; font-size: 12px;")
        right_line = QFrame()
        right_line.setFrameShape(QFrame.Shape.HLine)
        right_line.setStyleSheet("color: #30363d;")
        divider_row.addWidget(left_line)
        divider_row.addWidget(or_label)
        divider_row.addWidget(right_line)
        layout.addLayout(divider_row)
        layout.addSpacing(20)

        # ── GitHub URL Input ─────────────────────────────────────────────
        github_frame = QFrame()
        github_frame.setObjectName("githubFrame")
        github_frame.setFixedWidth(500)
        github_layout = QHBoxLayout(github_frame)
        github_layout.setContentsMargins(0, 0, 0, 0)
        github_layout.setSpacing(8)

        self.github_input = QLineEdit()
        self.github_input.setObjectName("githubInput")
        self.github_input.setPlaceholderText("https://github.com/username/repository")
        self.github_input.setFixedHeight(42)
        self.github_input.returnPressed.connect(self._load_github)
        github_layout.addWidget(self.github_input)

        load_btn = QPushButton("Load")
        load_btn.setObjectName("loadGithubBtn")
        load_btn.setFixedSize(80, 42)
        load_btn.clicked.connect(self._load_github)
        github_layout.addWidget(load_btn)

        layout.addWidget(github_frame, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(32)

        # ── Feature Highlights ───────────────────────────────────────────
        features_row = QHBoxLayout()
        features_row.setSpacing(16)
        features = [
            ("🗺️", "Mind Map", "Visual file structure"),
            ("🤖", "AI Explain", "Plain English summaries"),
            ("🕸️", "Call Graph", "Function dependencies"),
            ("⚡", "Any Language", "Python, JS, Java, Go..."),
        ]
        for emoji, title, desc in features:
            card = self._make_feature_card(emoji, title, desc)
            features_row.addWidget(card)
        layout.addLayout(features_row)

    def _make_feature_card(self, emoji: str, title: str, desc: str) -> QFrame:
        card = QFrame()
        card.setObjectName("featureCard")
        card.setFixedSize(110, 90)
        card_layout = QVBoxLayout(card)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.setSpacing(4)

        em = QLabel(emoji)
        em.setAlignment(Qt.AlignmentFlag.AlignCenter)
        em.setFont(QFont("Segoe UI Emoji", 20))
        card_layout.addWidget(em)

        t = QLabel(title)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        card_layout.addWidget(t)

        d = QLabel(desc)
        d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d.setStyleSheet("color: #8b949e; font-size: 8px;")
        card_layout.addWidget(d)

        return card

    def _browse_folder(self, event=None):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Project Folder", os.path.expanduser("~")
        )
        if folder:
            self.open_folder_requested.emit(folder)

    def _load_github(self):
        url = self.github_input.text().strip()
        if url:
            self.open_github_requested.emit(url)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop_zone.setStyleSheet(
                "#dropZone { border: 2px dashed #7c3aed; background: #1a1060; }"
            )

    def dragLeaveEvent(self, event):
        self.drop_zone.setStyleSheet("")

    def dropEvent(self, event: QDropEvent):
        self.drop_zone.setStyleSheet("")
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isdir(path):
                self.open_folder_requested.emit(path)
            else:
                # Use the parent directory
                self.open_folder_requested.emit(os.path.dirname(path))
