"""
explanation_panel.py — AI explanation display with typewriter animation.
Shows project / file / function explanations streamed from Gemini.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import QTextEdit


class ExplanationPanel(QWidget):
    
    """
    Displays AI-generated explanations with a typewriter streaming effect.
    Shows breadcrumb navigation and copy-to-clipboard functionality.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self._display_text = ""
        self._typewriter_timer = QTimer()
        self._typewriter_buffer = ""
        self._typewriter_idx = 0
        self._init_ui()
        self._typewriter_timer.timeout.connect(self._typewriter_tick)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ───────────────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("explanationHeader")
        header.setFixedHeight(44)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)

        self.breadcrumb_label = QLabel("📖 Project Overview")
        self.breadcrumb_label.setObjectName("breadcrumbLabel")
        self.breadcrumb_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
        header_layout.addWidget(self.breadcrumb_label)
        header_layout.addStretch()

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setStyleSheet("color: #7c3aed; font-size: 10px;")
        header_layout.addWidget(self.status_label)

        self.copy_btn = QPushButton("📋 Copy")
        self.copy_btn.setObjectName("copyBtn")
        self.copy_btn.setFixedSize(70, 28)
        self.copy_btn.clicked.connect(self._copy_text)
        header_layout.addWidget(self.copy_btn)

        layout.addWidget(header)

        # ── Divider ──────────────────────────────────────────────────────
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("divider")
        layout.addWidget(divider)

        # ── Text Display ─────────────────────────────────────────────────
        self.text_edit = QTextEdit()
        self.text_edit.setObjectName("explanationText")
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Segoe UI", 10))
        self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Welcome message
        self.text_edit.setMarkdown(
            "## 👋 Welcome to Know Your Projects\n\n"
            "Open a project folder or paste a GitHub URL to get started.\n\n"
            "Once loaded:\n"
            "- 📁 Click any **file** in the tree to get an AI explanation\n"
            "- 🔍 Click any **function** to see what it does\n"
            "- 🕸️ View the **dependency graph** to see how code connects\n\n"
            "*Your Gemini API key is required for explanations.*\n"
            "*Configure it in Settings (⚙️ top-right).*"
        )
        layout.addWidget(self.text_edit)

    def set_breadcrumb(self, text: str):
        self.breadcrumb_label.setText(text)

    def start_streaming(self, breadcrumb: str = ""):
        """Prepare for streaming explanation."""
        if breadcrumb:
            self.breadcrumb_label.setText(breadcrumb)
        self._full_text = ""
        self._typewriter_buffer = ""
        self._typewriter_idx = 0
        self.text_edit.clear()
        self.status_label.setText("✨ Generating...")
        self._typewriter_timer.start(12)  # 12ms per tick

    def append_chunk(self, chunk: str):
        """Add a new chunk of text from the streaming response."""
        self._typewriter_buffer += chunk

    def finish_streaming(self, full_text: str):
        """Called when streaming is complete."""
        self._full_text = full_text
        self._typewriter_buffer = full_text
        # Let timer finish naturally
        QTimer.singleShot(500, self._finalize)

    def _typewriter_tick(self):
        """Advance typewriter by a few characters each tick."""
        if self._typewriter_idx >= len(self._typewriter_buffer):
            return

        # Advance 3 chars per tick for smooth but fast effect
        end = min(self._typewriter_idx + 3, len(self._typewriter_buffer))
        new_chars = self._typewriter_buffer[self._typewriter_idx:end]
        self._typewriter_idx = end
        self._display_text += new_chars

        # Render as markdown
        self.text_edit.setMarkdown(self._display_text)
        # Scroll to bottom
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.text_edit.setTextCursor(cursor)

    def _finalize(self):
        """Finalize display and stop timer."""
        self._typewriter_timer.stop()
        self.text_edit.setMarkdown(self._full_text)
        self.status_label.setText("✅ Done")
        QTimer.singleShot(2000, lambda: self.status_label.setText(""))

    def show_error(self, message: str):
        """Display an error message."""
        self._typewriter_timer.stop()
        self.status_label.setText("❌ Error")
        self.text_edit.setMarkdown(f"## ❌ Error\n\n{message}")

    def show_loading(self, message: str = "Analyzing..."):
        """Show a loading state."""
        self._typewriter_timer.stop()
        self.text_edit.setMarkdown(f"*⏳ {message}*")
        self.status_label.setText("Loading...")

    def set_static_text(self, markdown_text: str, breadcrumb: str = ""):
        """Display text without typewriter (instant)."""
        self._typewriter_timer.stop()
        if breadcrumb:
            self.breadcrumb_label.setText(breadcrumb)
        self._full_text = markdown_text
        self.text_edit.setMarkdown(markdown_text)
        self.status_label.setText("")

    def _copy_text(self):
        """Copy explanation to clipboard."""
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._full_text)
        self.copy_btn.setText("✅ Copied!")
        QTimer.singleShot(2000, lambda: self.copy_btn.setText("📋 Copy"))
