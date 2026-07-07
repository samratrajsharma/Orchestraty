"""
file_tree_panel.py — Left panel: interactive file tree explorer.
Shows project hierarchy, search, and selection events.
"""
import os
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QLineEdit, QFrame,
    QSizePolicy, QMenu, QHeaderView
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QIcon, QBrush

from core.file_tree_builder import TreeNode, LANGUAGE_MAP


# Language → color map for badges
LANG_COLORS = {
    
    "Python":     "#3572A5",
    "JavaScript": "#f1e05a",
    "TypeScript": "#2b7489",
    "Java":       "#b07219",
    "C++":        "#f34b7d",
    "C":          "#555555",
    "C#":         "#178600",
    "Go":         "#00ADD8",
    "Rust":       "#dea584",
    "Ruby":       "#701516",
    "PHP":        "#4F5D95",
    "Swift":      "#ffac45",
    "Kotlin":     "#A97BFF",
    "HTML":       "#e34c26",
    "CSS":        "#563d7c",
    "SCSS":       "#c6538c",
    "JSON":       "#292929",
    "YAML":       "#cb171e",
    "Markdown":   "#083fa1",
    "Shell":      "#89e051",
    "SQL":        "#e38c00",
}

# Language → emoji icon
LANG_ICONS = {
    "Python": "🐍",
    "JavaScript": "🟨",
    "TypeScript": "🔷",
    "Java": "☕",
    "C++": "⚙️",
    "C": "⚙️",
    "C#": "🔵",
    "Go": "🐹",
    "Rust": "🦀",
    "Ruby": "💎",
    "PHP": "🐘",
    "Swift": "🍎",
    "Kotlin": "🎯",
    "HTML": "🌐",
    "CSS": "🎨",
    "JSON": "📋",
    "YAML": "📄",
    "Markdown": "📝",
    "Shell": "💻",
    "SQL": "🗄️",
}


class FileTreePanel(QWidget):
    """
    Left panel: Project file tree with search, language badges, and selection signals.
    """

    file_selected = pyqtSignal(str, object)   # (file_path, tree_node)
    folder_selected = pyqtSignal(str)          # folder_path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root_node: TreeNode | None = None
        self._all_items: list = []
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ───────────────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("treePanelHeader")
        header.setFixedHeight(44)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)

        title = QLabel("📁 Project Files")
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.file_count_label = QLabel("")
        self.file_count_label.setStyleSheet("color: #8b949e; font-size: 10px;")
        header_layout.addWidget(self.file_count_label)

        layout.addWidget(header)

        # ── Search ───────────────────────────────────────────────────────
        search_frame = QFrame()
        search_frame.setObjectName("searchFrame")
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(8, 6, 8, 6)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("searchBox")
        self.search_box.setPlaceholderText("🔍 Search files...")
        self.search_box.setFixedHeight(30)
        self.search_box.textChanged.connect(self._on_search_changed)
        search_layout.addWidget(self.search_box)

        layout.addWidget(search_frame)

        # ── Tree Widget ──────────────────────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setObjectName("fileTree")
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(16)
        self.tree.setUniformRowHeights(False)
        self.tree.setFont(QFont("Segoe UI", 9))
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.setStyleSheet("""
            QTreeWidget::item { padding: 3px 0; }
            QTreeWidget::item:hover { background: #1c2128; }
            QTreeWidget::item:selected { background: #1f3a5f; color: white; }
        """)
        layout.addWidget(self.tree)

        # ── Stats ────────────────────────────────────────────────────────
        self.stats_bar = QFrame()
        self.stats_bar.setObjectName("statsBar")
        self.stats_bar.setFixedHeight(26)
        stats_layout = QHBoxLayout(self.stats_bar)
        stats_layout.setContentsMargins(12, 0, 12, 0)
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #484f58; font-size: 10px;")
        stats_layout.addWidget(self.stats_label)
        layout.addWidget(self.stats_bar)

    def load_tree(self, root_node: TreeNode, stats: dict):
        """Populate the tree widget from a TreeNode hierarchy."""
        self.tree.clear()
        self._all_items.clear()
        self._root_node = root_node

        # Root item
        root_item = self._make_item(root_node, parent=None)
        self.tree.addTopLevelItem(root_item)
        root_item.setExpanded(True)

        # Stats
        lang_str = ", ".join(
            f"{lang}: {count}"
            for lang, count in sorted(
                stats.get("languages", {}).items(),
                key=lambda x: x[1], reverse=True
            )[:3]
        )
        self.file_count_label.setText(f"{stats.get('total_files', 0)} files")
        self.stats_label.setText(
            f"📊 {stats.get('total_files', 0)} files | "
            f"Primary: {stats.get('primary_language', '?')} | {lang_str}"
        )

    def _make_item(self, node: TreeNode, parent) -> QTreeWidgetItem:
        """Recursively create QTreeWidgetItems."""
        if parent is None:
            item = QTreeWidgetItem()
        else:
            item = QTreeWidgetItem(parent)

        # Icon + label
        if node.is_dir:
            item.setText(0, f"📂 {node.name}")
            item.setForeground(0, QBrush(QColor("#e6edf3")))
        else:
            icon = LANG_ICONS.get(node.language, "📄")
            item.setText(0, f"{icon} {node.name}")
            # Color by language
            color = LANG_COLORS.get(node.language, "#8b949e")
            item.setForeground(0, QBrush(QColor(color) if color != "#292929" else QColor("#8b949e")))

        item.setData(0, Qt.ItemDataRole.UserRole, node)
        self._all_items.append(item)

        # Add children
        for child in node.children:
            child_item = self._make_item(child, item)
            # Auto-expand first level
            if node.parent is None:
                item.setExpanded(True)

        return item

    def _on_item_clicked(self, item: QTreeWidgetItem, col: int):
        node: TreeNode = item.data(0, Qt.ItemDataRole.UserRole)
        if node is None:
            return
        if not node.is_dir:
            self.file_selected.emit(node.path, node)
        else:
            self.folder_selected.emit(node.path)

    def _on_search_changed(self, text: str):
        self._search_timer.start(300)

    def _apply_filter(self):
        query = self.search_box.text().lower().strip()
        if not query:
            self._show_all()
            return
        for item in self._all_items:
            node: TreeNode = item.data(0, Qt.ItemDataRole.UserRole)
            if node is None:
                continue
            visible = query in node.name.lower()
            item.setHidden(not visible)
            if visible:
                self._reveal_parents(item)

    def _show_all(self):
        for item in self._all_items:
            item.setHidden(False)

    def _reveal_parents(self, item: QTreeWidgetItem):
        parent = item.parent()
        while parent:
            parent.setHidden(False)
            parent.setExpanded(True)
            parent = parent.parent()

    def _show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        node: TreeNode = item.data(0, Qt.ItemDataRole.UserRole)
        if node is None:
            return
        menu = QMenu(self)
        if not node.is_dir:
            analyze_act = menu.addAction("🤖 Analyze with AI")
            analyze_act.triggered.connect(lambda: self.file_selected.emit(node.path, node))
            copy_act = menu.addAction("📋 Copy Path")
            copy_act.triggered.connect(lambda: self._copy_path(node.path))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _copy_path(self, path: str):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(path)

    def select_file_by_path(self, file_path: str):
        """Programmatically select a file in the tree."""
        for item in self._all_items:
            node: TreeNode = item.data(0, Qt.ItemDataRole.UserRole)
            if node and node.path == file_path:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                break

    def clear(self):
        self.tree.clear()
        self._all_items.clear()
        self._root_node = None
        self.file_count_label.setText("")
        self.stats_label.setText("")
