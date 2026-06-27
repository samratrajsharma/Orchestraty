"""
dependency_window.py — Full-screen Dependency Graph Window.
Opens as a standalone window when user clicks "View Dependency Graph".
Features: interactive graph, node detail sidebar, search, filters, legend.
"""
import os
from typing import Optional
import networkx as nx

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QComboBox, QSizePolicy, QSplitter,
    QListWidget, QListWidgetItem, QLineEdit, QGroupBox,
    QScrollArea, QToolBar, QStatusBar, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QSize, QTimer
from PyQt6.QtGui import QFont, QColor, QIcon, QKeySequence, QAction

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

from ui.dependency_graph_panel import build_pyvis_html, _fallback_html, NODE_COLORS, EDGE_COLORS


class DependencyWindow(QMainWindow):
    """
    Standalone full-screen window for exploring the dependency graph.
    """

    def __init__(self, analyzer, project_name: str = "", parent=None):
        super().__init__(parent)
        self._analyzer = analyzer
        self._project_name = project_name
        self._current_scope = "full"   # "full" | "file" | "function"
        self._current_filter = "All"

        self.setWindowTitle(f"🕸 Dependency Graph — {project_name}")
        self.setMinimumSize(1100, 700)
        self.resize(1400, 860)

        self._init_ui()
        self._apply_styles()

        # Load full graph on open
        QTimer.singleShot(100, self.show_full_graph)

    # ──────────────────────────────────────────────────────────────────
    # UI CONSTRUCTION
    # ──────────────────────────────────────────────────────────────────

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Top toolbar ──────────────────────────────────────────────
        self._build_toolbar(root_layout)

        # ── Main area: graph | sidebar ───────────────────────────────
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(3)
        root_layout.addWidget(self.splitter)

        # Graph area
        graph_container = QWidget()
        graph_layout = QVBoxLayout(graph_container)
        graph_layout.setContentsMargins(0, 0, 0, 0)
        graph_layout.setSpacing(0)

        if HAS_WEBENGINE:
            self.web_view = QWebEngineView()
            self.web_view.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            graph_layout.addWidget(self.web_view)
        else:
            lbl = QLabel("⚠️ PyQt6-WebEngine not installed.\nRun: pip install PyQt6-WebEngine")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color:#8b949e; font-size:14px;")
            graph_layout.addWidget(lbl)

        self.splitter.addWidget(graph_container)

        # Right sidebar
        sidebar = self._build_sidebar()
        self.splitter.addWidget(sidebar)
        self.splitter.setSizes([950, 320])

        # ── Status bar ───────────────────────────────────────────────
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._update_stats()

    def _build_toolbar(self, parent_layout):
        bar = QFrame()
        bar.setObjectName("depWinToolbar")
        bar.setFixedHeight(52)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 0, 16, 0)
        bar_layout.setSpacing(10)

        # Title
        title = QLabel("🕸 Dependency Graph")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setObjectName("depWinTitle")
        bar_layout.addWidget(title)

        div = self._vline()
        bar_layout.addWidget(div)

        # Scope buttons
        self.btn_full = QPushButton("🌐 Full Project")
        self.btn_full.setObjectName("scopeBtn")
        self.btn_full.setCheckable(True)
        self.btn_full.setChecked(True)
        self.btn_full.clicked.connect(self.show_full_graph)

        self.btn_files = QPushButton("📄 Files Only")
        self.btn_files.setObjectName("scopeBtn")
        self.btn_files.setCheckable(True)
        self.btn_files.clicked.connect(self._show_files_only)

        self.btn_calls = QPushButton("🔗 Calls Only")
        self.btn_calls.setObjectName("scopeBtn")
        self.btn_calls.setCheckable(True)
        self.btn_calls.clicked.connect(self._show_calls_only)

        self._scope_btns = [self.btn_full, self.btn_files, self.btn_calls]
        for btn in self._scope_btns:
            bar_layout.addWidget(btn)

        bar_layout.addWidget(self._vline())

        # Search
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍  Search node...")
        self.search_box.setFixedWidth(200)
        self.search_box.setObjectName("depSearch")
        self.search_box.textChanged.connect(self._on_search)
        bar_layout.addWidget(self.search_box)

        bar_layout.addStretch()

        # Refresh
        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.setObjectName("depActionBtn")
        refresh_btn.clicked.connect(self._refresh)
        bar_layout.addWidget(refresh_btn)

        # Close
        close_btn = QPushButton("✕ Close")
        close_btn.setObjectName("depCloseBtn")
        close_btn.clicked.connect(self.close)
        bar_layout.addWidget(close_btn)

        parent_layout.addWidget(bar)

        # Thin divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("depDivider")
        parent_layout.addWidget(divider)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("depSidebar")
        sidebar.setMinimumWidth(260)
        sidebar.setMaximumWidth(380)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # ── Stats ────────────────────────────────────────────────────
        stats_group = QGroupBox("📊 Graph Stats")
        stats_group.setObjectName("depGroup")
        stats_layout = QVBoxLayout(stats_group)
        stats_layout.setSpacing(4)

        self.lbl_nodes = QLabel("Nodes: —")
        self.lbl_edges = QLabel("Edges: —")
        self.lbl_files = QLabel("Files: —")
        self.lbl_symbols = QLabel("Symbols: —")
        for lbl in [self.lbl_nodes, self.lbl_edges, self.lbl_files, self.lbl_symbols]:
            lbl.setObjectName("statLabel")
            stats_layout.addWidget(lbl)
        layout.addWidget(stats_group)

        # ── Filter by type ───────────────────────────────────────────
        filter_group = QGroupBox("🔎 Filter by Type")
        filter_group.setObjectName("depGroup")
        filter_layout = QVBoxLayout(filter_group)
        filter_layout.setSpacing(4)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Files", "Functions", "Classes", "Methods", "Imports"])
        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.filter_combo)
        layout.addWidget(filter_group)

        # ── Top connected nodes ───────────────────────────────────────
        top_group = QGroupBox("🏆 Most Connected Nodes")
        top_group.setObjectName("depGroup")
        top_layout = QVBoxLayout(top_group)
        top_layout.setSpacing(0)

        self.top_list = QListWidget()
        self.top_list.setObjectName("depList")
        self.top_list.setMaximumHeight(200)
        self.top_list.itemClicked.connect(self._on_top_node_clicked)
        top_layout.addWidget(self.top_list)
        layout.addWidget(top_group)

        # ── Node detail ───────────────────────────────────────────────
        detail_group = QGroupBox("📋 Node Detail")
        detail_group.setObjectName("depGroup")
        detail_layout = QVBoxLayout(detail_group)
        detail_layout.setSpacing(4)

        self.detail_name = QLabel("—")
        self.detail_name.setObjectName("detailName")
        self.detail_name.setWordWrap(True)
        self.detail_name.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        detail_layout.addWidget(self.detail_name)

        self.detail_type = QLabel("")
        self.detail_type.setObjectName("detailMeta")
        detail_layout.addWidget(self.detail_type)

        self.detail_connections = QLabel("")
        self.detail_connections.setObjectName("detailMeta")
        self.detail_connections.setWordWrap(True)
        detail_layout.addWidget(self.detail_connections)

        layout.addWidget(detail_group)

        # ── Legend ────────────────────────────────────────────────────
        legend_group = QGroupBox("🎨 Legend")
        legend_group.setObjectName("depGroup")
        legend_layout = QVBoxLayout(legend_group)
        legend_layout.setSpacing(3)

        legend_items = [
            ("file", "File"),
            ("function", "Function"),
            ("class", "Class"),
            ("method", "Method"),
            ("import", "Import"),
        ]
        for kind, label in legend_items:
            color = NODE_COLORS.get(kind, "#8b949e")
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 16px;")
            dot.setFixedWidth(20)
            row.addWidget(dot)
            txt = QLabel(label)
            txt.setObjectName("legendLabel")
            row.addWidget(txt)
            row.addStretch()
            legend_layout.addLayout(row)

        layout.addWidget(legend_group)
        layout.addStretch()

        return sidebar

    # ──────────────────────────────────────────────────────────────────
    # GRAPH DISPLAY
    # ──────────────────────────────────────────────────────────────────

    def show_full_graph(self):
        """Show the complete project dependency graph."""
        self._set_scope(self.btn_full)
        self._current_scope = "full"
        if not self._analyzer or not HAS_WEBENGINE:
            return
        graph = self._get_filtered_graph(self._analyzer.graph)
        self._render(graph, "Full Project")
        self._populate_top_list()

    def show_file_graph(self, file_path: str):
        """Show graph for a specific file."""
        self._current_scope = "file"
        if not self._analyzer or not HAS_WEBENGINE:
            return
        subgraph = self._analyzer.get_file_subgraph(file_path)
        self._render(subgraph, f"File: {os.path.basename(file_path)}")
        self._populate_top_list(subgraph)

    def show_function_graph(self, func_name: str, file_path: str):
        """Show graph centered on a function."""
        self._current_scope = "function"
        if not self._analyzer or not HAS_WEBENGINE:
            return
        node_id = f"{file_path}::{func_name}"
        subgraph = self._analyzer.get_subgraph_for_node(node_id, depth=2)
        self._render(subgraph, f"Function: {func_name}()")
        self._populate_top_list(subgraph)

    def _show_files_only(self):
        self._set_scope(self.btn_files)
        if not self._analyzer:
            return
        g = nx.DiGraph()
        for node, data in self._analyzer.graph.nodes(data=True):
            if data.get("kind") == "file":
                g.add_node(node, **data)
        for u, v, data in self._analyzer.graph.edges(data=True):
            if data.get("relation") == "imports" and u in g and v in g:
                g.add_edge(u, v, **data)
        self._render(g, "File Import Graph")
        self._populate_top_list(g)

    def _show_calls_only(self):
        self._set_scope(self.btn_calls)
        if not self._analyzer:
            return
        g = nx.DiGraph()
        for node, data in self._analyzer.graph.nodes(data=True):
            if data.get("kind") in ("function", "method"):
                g.add_node(node, **data)
        for u, v, data in self._analyzer.graph.edges(data=True):
            if data.get("relation") == "calls" and u in g and v in g:
                g.add_edge(u, v, **data)
        self._render(g, "Function Call Graph")
        self._populate_top_list(g)

    def _get_filtered_graph(self, graph: nx.DiGraph) -> nx.DiGraph:
        """Apply type filter to the graph."""
        f = self._current_filter
        if f == "All":
            return graph
        kind_map = {
            "Files": "file", "Functions": "function",
            "Classes": "class", "Methods": "method", "Imports": "import"
        }
        kind = kind_map.get(f)
        nodes = [n for n, d in graph.nodes(data=True) if d.get("kind") == kind]
        return graph.subgraph(nodes).copy()

    def _render(self, graph: nx.DiGraph, title: str):
        if not HAS_WEBENGINE:
            return
        # Limit size
        if graph.number_of_nodes() > 200:
            top = sorted(graph.degree(), key=lambda x: x[1], reverse=True)[:180]
            graph = graph.subgraph([n[0] for n in top]).copy()

        if graph.number_of_nodes() == 0:
            self._show_empty()
            return

        html = build_pyvis_html(graph, title)
        self.web_view.setHtml(html, QUrl("about:blank"))
        self._update_stats(graph)

    def _show_empty(self, msg: str = "No nodes to display for this filter"):
        if not HAS_WEBENGINE:
            return
        html = f"""<!DOCTYPE html><html>
<head><meta charset="utf-8">
<style>
body{{background:#0d1117;display:flex;justify-content:center;align-items:center;
     height:100vh;margin:0;font-family:'Segoe UI',sans-serif;color:#484f58;}}
.msg{{text-align:center;}}
.icon{{font-size:56px;margin-bottom:16px;}}
p{{font-size:14px;}}
</style></head><body>
<div class="msg"><div class="icon">🕸</div><p>{msg}</p></div>
</body></html>"""
        self.web_view.setHtml(html, QUrl("about:blank"))

    # ──────────────────────────────────────────────────────────────────
    # SIDEBAR HELPERS
    # ──────────────────────────────────────────────────────────────────

    def _populate_top_list(self, graph: nx.DiGraph = None):
        self.top_list.clear()
        if graph is None and self._analyzer:
            graph = self._analyzer.graph
        if graph is None:
            return
        top = sorted(graph.degree(), key=lambda x: x[1], reverse=True)[:15]
        for node_id, degree in top:
            data = graph.nodes.get(node_id, {})
            label = data.get("label", node_id.split("::")[-1])
            kind = data.get("kind", "?")
            color = NODE_COLORS.get(kind, "#8b949e")
            item = QListWidgetItem(f"● {label}  ({degree})")
            item.setForeground(QColor(color))
            item.setData(Qt.ItemDataRole.UserRole, (node_id, data, degree, graph))
            self.top_list.addItem(item)

    def _on_top_node_clicked(self, item: QListWidgetItem):
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not payload:
            return
        node_id, data, degree, graph = payload
        label = data.get("label", node_id.split("::")[-1])
        kind = data.get("kind", "unknown")
        preds = list(graph.predecessors(node_id))
        succs = list(graph.successors(node_id))

        self.detail_name.setText(label)
        self.detail_type.setText(f"Type: {kind}  |  Connections: {degree}")
        conn_text = ""
        if succs:
            conn_text += f"→ Calls/imports: {', '.join(s.split('::')[-1] for s in succs[:5])}"
            if len(succs) > 5:
                conn_text += f" +{len(succs)-5} more"
        if preds:
            if conn_text:
                conn_text += "\n"
            conn_text += f"← Called by: {', '.join(p.split('::')[-1] for p in preds[:5])}"
            if len(preds) > 5:
                conn_text += f" +{len(preds)-5} more"
        self.detail_connections.setText(conn_text or "No connections")

        # Focus graph on this node
        if self._analyzer:
            subgraph = self._analyzer.get_subgraph_for_node(node_id, depth=2)
            self._render(subgraph, f"Focus: {label}")

    def _on_search(self, text: str):
        if not text or not self._analyzer:
            return
        text_lower = text.lower()
        matches = [
            (n, d) for n, d in self._analyzer.graph.nodes(data=True)
            if text_lower in d.get("label", "").lower()
        ]
        self.top_list.clear()
        for node_id, data in matches[:20]:
            label = data.get("label", node_id.split("::")[-1])
            kind = data.get("kind", "?")
            color = NODE_COLORS.get(kind, "#8b949e")
            degree = self._analyzer.graph.degree(node_id)
            item = QListWidgetItem(f"🔍 {label}  ({kind})")
            item.setForeground(QColor(color))
            item.setData(Qt.ItemDataRole.UserRole,
                         (node_id, data, degree, self._analyzer.graph))
            self.top_list.addItem(item)

    def _on_filter_changed(self, text: str):
        self._current_filter = text
        self._refresh()

    def _refresh(self):
        if self._current_scope == "full":
            self.show_full_graph()
        elif self._current_scope == "files":
            self._show_files_only()
        elif self._current_scope == "calls":
            self._show_calls_only()

    def _update_stats(self, graph: nx.DiGraph = None):
        if graph is None and self._analyzer:
            stats = self._analyzer.get_stats()
            self.lbl_nodes.setText(f"Nodes: {stats['nodes']}")
            self.lbl_edges.setText(f"Edges: {stats['edges']}")
            self.lbl_files.setText(f"Files: {stats['files']}")
            self.lbl_symbols.setText(f"Symbols: {stats['symbols']}")
            self.status_bar.showMessage(
                f"Project: {self._project_name}  |  "
                f"Nodes: {stats['nodes']}  |  Edges: {stats['edges']}"
            )
        elif graph:
            n = graph.number_of_nodes()
            e = graph.number_of_edges()
            self.lbl_nodes.setText(f"Nodes: {n}")
            self.lbl_edges.setText(f"Edges: {e}")
            self.status_bar.showMessage(f"Showing {n} nodes, {e} edges")

    def _set_scope(self, active_btn: QPushButton):
        for btn in self._scope_btns:
            btn.setChecked(btn is active_btn)

    def _vline(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedHeight(24)
        line.setObjectName("depVLine")
        return line

    # ──────────────────────────────────────────────────────────────────
    # STYLES
    # ──────────────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet("""
        QMainWindow, QWidget {
            background: #0d1117;
            color: #e6edf3;
            font-family: 'Segoe UI', sans-serif;
        }
        #depWinToolbar {
            background: #161b22;
            border-bottom: 1px solid #30363d;
        }
        #depWinTitle {
            color: #e6edf3;
        }
        #depDivider {
            background: #21262d;
            max-height: 1px;
            border: none;
        }
        #depVLine {
            color: #30363d;
        }
        #depSidebar {
            background: #161b22;
            border-left: 1px solid #30363d;
        }
        #depGroup {
            color: #8b949e;
            font-size: 11px;
            border: 1px solid #21262d;
            border-radius: 6px;
            margin-top: 4px;
            padding-top: 4px;
            background: #0d1117;
        }
        #depGroup::title {
            color: #8b949e;
            subcontrol-origin: margin;
            padding: 0 4px;
        }
        #statLabel {
            color: #c9d1d9;
            font-size: 12px;
        }
        #detailName {
            color: #e6edf3;
        }
        #detailMeta {
            color: #8b949e;
            font-size: 11px;
        }
        #legendLabel {
            color: #c9d1d9;
            font-size: 11px;
        }
        #depList {
            background: #0d1117;
            border: 1px solid #21262d;
            border-radius: 4px;
            color: #c9d1d9;
            font-size: 11px;
        }
        #depList::item:hover {
            background: #1f2937;
        }
        #depList::item:selected {
            background: #1f6feb33;
        }
        #depSearch {
            background: #21262d;
            border: 1px solid #30363d;
            border-radius: 5px;
            color: #e6edf3;
            padding: 4px 10px;
            font-size: 12px;
        }
        #depSearch:focus {
            border: 1px solid #1f6feb;
        }
        #scopeBtn {
            background: #21262d;
            color: #c9d1d9;
            border: 1px solid #30363d;
            border-radius: 5px;
            padding: 5px 12px;
            font-size: 12px;
        }
        #scopeBtn:hover {
            background: #2d333b;
            border-color: #1f6feb;
        }
        #scopeBtn:checked {
            background: #1f6feb;
            color: #ffffff;
            border-color: #1f6feb;
        }
        #depActionBtn {
            background: #21262d;
            color: #c9d1d9;
            border: 1px solid #30363d;
            border-radius: 5px;
            padding: 5px 14px;
            font-size: 12px;
        }
        #depActionBtn:hover {
            background: #2d333b;
        }
        #depCloseBtn {
            background: transparent;
            color: #f78166;
            border: 1px solid #f78166;
            border-radius: 5px;
            padding: 5px 14px;
            font-size: 12px;
        }
        #depCloseBtn:hover {
            background: #f7816622;
        }
        QComboBox {
            background: #21262d;
            border: 1px solid #30363d;
            border-radius: 4px;
            color: #e6edf3;
            padding: 4px 8px;
            font-size: 12px;
        }
        QComboBox::drop-down { border: none; }
        QStatusBar {
            background: #161b22;
            color: #8b949e;
            font-size: 11px;
            border-top: 1px solid #21262d;
        }
        QSplitter::handle {
            background: #21262d;
        }
        """)
