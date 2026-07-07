"""
function_popup.py — Popup dialog for detailed function analysis.
Shows function code, AI explanation, callers, and callees.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QWidget, QListWidget,
    QListWidgetItem, QFrame, QSplitter, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QSyntaxHighlighter, QTextCharFormat, QColor
from PyQt6.QtWidgets import QTextEdit, QPlainTextEdit

from ui.explanation_panel import ExplanationPanel


class FunctionPopup(QDialog):
    
    """
    Detailed popup for a specific function.
    Shows: code, AI explanation, callers, callees, "Show in Graph" button.
    """

    show_in_graph = pyqtSignal(str, str)   # (func_name, file_path)
    navigate_to_function = pyqtSignal(str, str)  # (func_name, file_path)

    def __init__(self, func_name: str, file_path: str, func_code: str,
                 language: str, callers: list, callees: list,
                 ai_explainer=None, parent=None):
        super().__init__(parent)
        self.func_name = func_name
        self.file_path = file_path
        self.func_code = func_code
        self.language = language
        self.callers = callers
        self.callees = callees
        self.ai_explainer = ai_explainer

        self.setWindowTitle(f"Function: {func_name}()")
        self.setMinimumSize(700, 550)
        self.resize(800, 600)
        self._init_ui()

        if self.ai_explainer:
            self._request_explanation()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Title bar ────────────────────────────────────────────────────
        title_bar = QFrame()
        title_bar.setObjectName("popupTitleBar")
        title_bar.setFixedHeight(56)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(20, 0, 20, 0)

        icon_label = QLabel("⚡")
        icon_label.setFont(QFont("Segoe UI", 18))
        title_layout.addWidget(icon_label)

        title_text = QVBoxLayout()
        func_label = QLabel(f"{self.func_name}()")
        func_label.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        func_label.setObjectName("popupFuncName")

        import os
        file_label = QLabel(os.path.basename(self.file_path))
        file_label.setObjectName("popupFileName")
        file_label.setStyleSheet("color: #8b949e; font-size: 10px;")

        title_text.addWidget(func_label)
        title_text.addWidget(file_label)
        title_layout.addLayout(title_text)
        title_layout.addStretch()

        graph_btn = QPushButton("🕸 Show in Graph")
        graph_btn.setObjectName("graphBtn")
        graph_btn.clicked.connect(
            lambda: self.show_in_graph.emit(self.func_name, self.file_path))
        title_layout.addWidget(graph_btn)

        layout.addWidget(title_bar)

        # ── Tab Widget ───────────────────────────────────────────────────
        tabs = QTabWidget()
        tabs.setObjectName("functionTabs")
        layout.addWidget(tabs)

        # Tab 1: Explanation
        explanation_tab = QWidget()
        exp_layout = QVBoxLayout(explanation_tab)
        exp_layout.setContentsMargins(0, 0, 0, 0)
        self.explanation = ExplanationPanel()
        exp_layout.addWidget(self.explanation)
        tabs.addTab(explanation_tab, "🤖 AI Explanation")

        # Tab 2: Source Code
        code_tab = QWidget()
        code_layout = QVBoxLayout(code_tab)
        code_layout.setContentsMargins(8, 8, 8, 8)

        self.code_view = QPlainTextEdit()
        self.code_view.setObjectName("functionCode")
        self.code_view.setReadOnly(True)
        self.code_view.setFont(QFont("Cascadia Code", 10))
        self.code_view.setPlainText(self.func_code)
        code_layout.addWidget(self.code_view)
        tabs.addTab(code_tab, "📄 Source Code")

        # Tab 3: Call Graph
        calls_tab = QWidget()
        calls_layout = QHBoxLayout(calls_tab)
        calls_layout.setContentsMargins(16, 16, 16, 16)
        calls_layout.setSpacing(16)

        # Callers
        callers_group = QVBoxLayout()
        callers_title = QLabel("📥 Called By")
        callers_title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        callers_group.addWidget(callers_title)
        self.callers_list = QListWidget()
        self.callers_list.setObjectName("callsList")
        for caller in self.callers:
            item = QListWidgetItem(f"⟵ {caller}")
            self.callers_list.addItem(item)
        if not self.callers:
            self.callers_list.addItem(QListWidgetItem("(No callers found)"))
        callers_group.addWidget(self.callers_list)
        calls_layout.addLayout(callers_group)

        # Divider
        vline = QFrame()
        vline.setFrameShape(QFrame.Shape.VLine)
        calls_layout.addWidget(vline)

        # Callees
        callees_group = QVBoxLayout()
        callees_title = QLabel("📤 Calls")
        callees_title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        callees_group.addWidget(callees_title)
        self.callees_list = QListWidget()
        self.callees_list.setObjectName("callsList")
        for callee in self.callees:
            item = QListWidgetItem(f"⟶ {callee}")
            self.callees_list.addItem(item)
        if not self.callees:
            self.callees_list.addItem(QListWidgetItem("(No callees found)"))
        callees_group.addWidget(self.callees_list)
        calls_layout.addLayout(callees_group)

        tabs.addTab(calls_tab, "🔗 Call Graph")

    def _request_explanation(self):
        """Request AI explanation for this function."""
        self.explanation.start_streaming(
            f"⚡ {self.func_name}() — {self.language}"
        )
        self.ai_explainer.explanation_chunk.connect(self.explanation.append_chunk)
        self.ai_explainer.explanation_done.connect(self.explanation.finish_streaming)
        self.ai_explainer.explanation_error.connect(self.explanation.show_error)
        self.ai_explainer.explain_function(
            self.func_name, self.file_path, self.language, self.func_code
        )
