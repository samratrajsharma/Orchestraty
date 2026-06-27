"""
code_viewer.py — Syntax-highlighted code viewer with clickable function names.
Center panel of the main window.
"""
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QSplitter, QSizePolicy,
    QScrollBar, QPlainTextEdit, QToolTip
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint, QRect
from PyQt6.QtGui import (
    QFont, QSyntaxHighlighter, QTextCharFormat, QColor,
    QTextCursor, QPainter, QTextBlockUserData
)
from PyQt6.QtWidgets import QTextEdit

from core.code_parser import ParsedFile, Symbol


# ─── Syntax Highlighter ───────────────────────────────────────────────────────

PYTHON_KEYWORDS = [
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
    "while", "with", "yield", "self", "cls",
]

JS_KEYWORDS = [
    "break", "case", "catch", "class", "const", "continue", "debugger",
    "default", "delete", "do", "else", "export", "extends", "finally",
    "for", "function", "if", "import", "in", "instanceof", "let",
    "new", "null", "return", "static", "super", "switch", "this",
    "throw", "try", "typeof", "undefined", "var", "void", "while",
    "with", "yield", "async", "await", "of", "from",
]


class SyntaxHighlighter(QSyntaxHighlighter):
    """Universal syntax highlighter supporting multiple languages."""

    def __init__(self, document, language: str = "Python"):
        super().__init__(document)
        self.language = language
        self._rules = []
        self._setup_rules()

    def _fmt(self, color: str, bold: bool = False, italic: bool = False):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        if italic:
            fmt.setFontItalic(True)
        return fmt

    def _setup_rules(self):
        lang = self.language

        # Keywords
        keywords = PYTHON_KEYWORDS if lang == "Python" else JS_KEYWORDS
        kw_fmt = self._fmt("#ff7b72", bold=True)
        for kw in keywords:
            import re
            self._rules.append((re.compile(r"\b" + kw + r"\b"), kw_fmt))

        # Strings
        str_fmt = self._fmt("#a5d6ff")
        import re
        self._rules.append((re.compile(r'"""[\s\S]*?"""'), str_fmt))
        self._rules.append((re.compile(r"'''[\s\S]*?'''"), str_fmt))
        self._rules.append((re.compile(r'"(?:[^"\\]|\\.)*"'), str_fmt))
        self._rules.append((re.compile(r"'(?:[^'\\]|\\.)*'"), str_fmt))

        # Comments
        comment_fmt = self._fmt("#8b949e", italic=True)
        if lang == "Python":
            self._rules.append((re.compile(r"#[^\n]*"), comment_fmt))
        else:
            self._rules.append((re.compile(r"//[^\n]*"), comment_fmt))
            self._rules.append((re.compile(r"/\*[\s\S]*?\*/"), comment_fmt))

        # Numbers
        num_fmt = self._fmt("#79c0ff")
        self._rules.append((re.compile(r"\b\d+\.?\d*\b"), num_fmt))

        # Function/class definitions
        def_fmt = self._fmt("#d2a8ff", bold=True)
        self._rules.append((re.compile(r"\bdef\s+(\w+)"), def_fmt))
        self._rules.append((re.compile(r"\bclass\s+(\w+)"), def_fmt))
        self._rules.append((re.compile(r"\bfunction\s+(\w+)"), def_fmt))
        self._rules.append((re.compile(r"\bconst\s+(\w+)\s*="), def_fmt))

        # Decorators
        dec_fmt = self._fmt("#ffa657")
        self._rules.append((re.compile(r"@\w+"), dec_fmt))

        # Builtins / types
        builtin_fmt = self._fmt("#79c0ff")
        builtins = ["print", "len", "range", "str", "int", "float", "list",
                    "dict", "tuple", "set", "bool", "type", "isinstance",
                    "super", "property", "staticmethod", "classmethod"]
        for b in builtins:
            self._rules.append((re.compile(r"\b" + b + r"\b"), builtin_fmt))

    def highlightBlock(self, text: str):
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


# ─── Line Number Area ─────────────────────────────────────────────────────────

class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        from PyQt6.QtCore import QSize
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self.editor.line_number_area_paint_event(event)


# ─── Code Editor with Line Numbers ────────────────────────────────────────────

class CodeEditor(QPlainTextEdit):
    """Code editor with line numbers and syntax highlighting."""

    function_clicked = pyqtSignal(str, int)  # (func_name, line_number)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        self._symbols: list = []
        self._highlighter = None

        self.setFont(QFont("Cascadia Code, Consolas, Courier New", 10))
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(32)

        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self.update_line_number_area_width(0)

    def set_content(self, code: str, language: str, symbols: list):
        self._symbols = symbols
        self.setPlainText(code)
        if self._highlighter:
            self._highlighter.setDocument(None)
        self._highlighter = SyntaxHighlighter(self.document(), language)

    def line_number_area_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(
                0, rect.y(), self.line_number_area.width(), rect.height()
            )
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(
            QRect(cr.left(), cr.top(),
                  self.line_number_area_width(), cr.height())
        )

    def line_number_area_paint_event(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#161b22"))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(
            self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        height = self.fontMetrics().height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#484f58"))
                painter.setFont(QFont("Cascadia Code", 9))
                painter.drawText(
                    0, top,
                    self.line_number_area.width() - 4, height,
                    Qt.AlignmentFlag.AlignRight,
                    number
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def _highlight_current_line(self):
        from PyQt6.QtWidgets import QTextEdit
        extra_selections = []
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            line_color = QColor("#1c2128")
            selection.format.setBackground(line_color)
            selection.format.setProperty(
                QTextCharFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            cursor = self.cursorForPosition(event.pos())
            line = cursor.blockNumber() + 1
            # Check if we clicked near a function definition
            for sym in self._symbols:
                if sym.kind in ("function", "method"):
                    if abs(sym.line_start - line) <= 1:
                        self.function_clicked.emit(sym.name, sym.line_start)
                        return


# ─── Code Viewer Panel ────────────────────────────────────────────────────────

class CodeViewerPanel(QWidget):
    """
    Full code viewer panel with:
    - Syntax highlighted code editor (top)
    - AI explanation panel (bottom)
    """

    function_clicked = pyqtSignal(str, int)  # (func_name, line_no)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_file: str = ""
        self._current_language: str = ""
        self._current_parsed = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── File Info Bar ─────────────────────────────────────────────────
        info_bar = QFrame()
        info_bar.setObjectName("fileInfoBar")
        info_bar.setFixedHeight(38)
        info_layout = QHBoxLayout(info_bar)
        info_layout.setContentsMargins(12, 0, 12, 0)

        self.file_label = QLabel("No file selected")
        self.file_label.setObjectName("fileLabel")
        self.file_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
        info_layout.addWidget(self.file_label)

        info_layout.addStretch()

        self.lang_badge = QLabel("")
        self.lang_badge.setObjectName("langBadge")
        self.lang_badge.setFixedHeight(22)
        self.lang_badge.setStyleSheet(
            "background: #7c3aed; color: white; border-radius: 4px; "
            "padding: 0 8px; font-size: 10px; font-weight: bold;"
        )
        self.lang_badge.hide()
        info_layout.addWidget(self.lang_badge)

        self.lines_label = QLabel("")
        self.lines_label.setStyleSheet("color: #8b949e; font-size: 10px; margin-left: 8px;")
        info_layout.addWidget(self.lines_label)

        layout.addWidget(info_bar)

        # ── Code Editor ──────────────────────────────────────────────────
        self.editor = CodeEditor()
        self.editor.function_clicked.connect(self.function_clicked)
        layout.addWidget(self.editor)

        # ── Hint Bar ─────────────────────────────────────────────────────
        hint_bar = QFrame()
        hint_bar.setObjectName("hintBar")
        hint_bar.setFixedHeight(26)
        hint_layout = QHBoxLayout(hint_bar)
        hint_layout.setContentsMargins(12, 0, 12, 0)
        hint_label = QLabel("💡 Click on a function name to analyze it in detail")
        hint_label.setStyleSheet("color: #484f58; font-size: 10px; font-style: italic;")
        hint_layout.addWidget(hint_label)
        layout.addWidget(hint_bar)

    def load_file(self, file_path: str, parsed_file=None):
        """Load and display a file."""
        self._current_file = file_path
        filename = os.path.basename(file_path)

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except (IOError, OSError):
            content = f"# Could not read file: {file_path}"

        language = "Text"
        symbols = []
        if parsed_file:
            language = parsed_file.language or "Text"
            symbols = parsed_file.symbols
            self._current_parsed = parsed_file
        self._current_language = language

        self.file_label.setText(f"📄 {filename}")
        self.lang_badge.setText(language)
        self.lang_badge.show()
        line_count = content.count("\n") + 1
        self.lines_label.setText(f"{line_count} lines")

        self.editor.set_content(content, language, symbols)

    def clear(self):
        self.editor.setPlainText("")
        self.file_label.setText("No file selected")
        self.lang_badge.hide()
        self.lines_label.setText("")
        self._current_file = ""
        self._current_parsed = None

    def get_current_file(self) -> str:
        return self._current_file

    def get_current_parsed(self):
        return self._current_parsed
