"""
dependency_graph_panel.py — Interactive dependency/call graph panel.
Uses PyVis + QWebEngineView to render interactive HTML graphs.
"""
import os
import json
import tempfile
from pathlib import Path
from typing import Optional
import networkx as nx

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QComboBox, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QUrl
from PyQt6.QtGui import QFont

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False


# Color scheme for graph nodes
NODE_COLORS = {
    "file": "#1f6feb",        # Blue
    "function": "#3fb950",    # Green
    "method": "#a371f7",      # Purple
    "class": "#f78166",       # Red-orange
    "import": "#d29922",      # Yellow
    "default": "#8b949e",     # Gray
}

EDGE_COLORS = {
    "calls": "#3fb950",
    "imports": "#1f6feb",
    "contains": "#484f58",
    "default": "#484f58",
}


def build_pyvis_html(graph: nx.DiGraph, title: str = "Dependency Graph") -> str:
    """Build a PyVis-style interactive HTML graph."""
    try:
        from pyvis.network import Network
        net = Network(
            height="100%",
            width="100%",
            bgcolor="#0d1117",
            font_color="#e6edf3",
            directed=True,
        )
        net.set_options("""
        {
          "nodes": {
            "borderWidth": 2,
            "shadow": true,
            "font": { "size": 13, "face": "Segoe UI" }
          },
          "edges": {
            "arrows": { "to": { "enabled": true, "scaleFactor": 0.8 } },
            "smooth": { "type": "curvedCW", "roundness": 0.2 },
            "shadow": true
          },
          "physics": {
            "enabled": true,
            "forceAtlas2Based": {
              "gravitationalConstant": -80,
              "springLength": 120,
              "springConstant": 0.08
            },
            "solver": "forceAtlas2Based",
            "stabilization": { "iterations": 100 }
          },
          "interaction": {
            "hover": true,
            "navigationButtons": true,
            "keyboard": true
          }
        }
        """)

        for node_id, data in graph.nodes(data=True):
            kind = data.get("kind", "default")
            label = data.get("label", str(node_id))
            if len(label) > 25:
                label = label[:22] + "..."
            color = NODE_COLORS.get(kind, NODE_COLORS["default"])
            size = 20 + min(graph.degree(node_id) * 3, 30)
            net.add_node(
                str(node_id),
                label=label,
                color=color,
                size=size,
                title=f"{kind}: {data.get('label', node_id)}",
                shape="dot" if kind in ("function", "method") else "box",
            )

        for u, v, data in graph.edges(data=True):
            relation = data.get("relation", "default")
            color = EDGE_COLORS.get(relation, EDGE_COLORS["default"])
            net.add_edge(str(u), str(v), color=color, title=relation)

        # Save to temp file
        tmp_file = tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        )
        net.save_graph(tmp_file.name)
        tmp_file.close()
        with open(tmp_file.name, "r", encoding="utf-8") as f:
            html = f.read()
        os.unlink(tmp_file.name)
        return html

    except ImportError:
        return _fallback_html(graph)


def _fallback_html(graph: nx.DiGraph) -> str:
    """Fallback HTML when PyVis is not installed."""
    nodes_data = []
    for node_id, data in graph.nodes(data=True):
        kind = data.get("kind", "default")
        label = data.get("label", str(node_id))
        color = NODE_COLORS.get(kind, NODE_COLORS["default"])
        nodes_data.append({"id": str(node_id), "label": label[:30], "color": color})

    edges_data = []
    for u, v, data in graph.edges(data=True):
        edges_data.append({"from": str(u), "to": str(v)})

    nodes_js = json.dumps(nodes_data)
    edges_js = json.dumps(edges_data)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Dependency Graph</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.js"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.css" rel="stylesheet">
<style>
  body {{ background: #0d1117; margin: 0; }}
  #graph {{ width: 100%; height: 100vh; }}
</style>
</head>
<body>
<div id="graph"></div>
<script>
var nodes = new vis.DataSet({nodes_js});
var edges = new vis.DataSet({edges_js});
var options = {{
  nodes: {{ font: {{ color: '#e6edf3', size: 14 }}, borderWidth: 2 }},
  edges: {{ arrows: 'to', color: '#3fb950' }},
  physics: {{ enabled: true }},
  background: {{ color: '#0d1117' }}
}};
new vis.Network(document.getElementById('graph'), {{nodes, edges}}, options);
</script>
</body>
</html>"""


class DependencyGraphPanel(QWidget):
    """
    Right panel showing the interactive dependency graph.
    Renders via QWebEngineView with PyVis HTML.
    """

    node_selected = pyqtSignal(str)   # node_id selected in graph
    open_fullscreen = pyqtSignal()    # user wants full-screen view

    def __init__(self, parent=None):
        super().__init__(parent)
        self._analyzer = None
        self._current_node = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ───────────────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("graphHeader")
        header.setFixedHeight(44)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)

        title = QLabel("🕸 Dependency Graph")
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.scope_combo = QComboBox()
        self.scope_combo.setObjectName("scopeCombo")
        self.scope_combo.addItems(["File View", "Full Project"])
        self.scope_combo.setFixedWidth(110)
        self.scope_combo.currentTextChanged.connect(self._on_scope_changed)
        header_layout.addWidget(self.scope_combo)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setFixedSize(30, 30)
        self.refresh_btn.setToolTip("Refresh Graph")
        self.refresh_btn.clicked.connect(self._refresh)
        header_layout.addWidget(self.refresh_btn)

        self.fullscreen_btn = QPushButton("⛶")
        self.fullscreen_btn.setFixedSize(30, 30)
        self.fullscreen_btn.setToolTip("Open Full Screen")
        self.fullscreen_btn.clicked.connect(self.open_fullscreen)
        header_layout.addWidget(self.fullscreen_btn)

        layout.addWidget(header)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("divider")
        layout.addWidget(divider)

        # ── Graph View or Fallback ───────────────────────────────────────
        if HAS_WEBENGINE:
            self.web_view = QWebEngineView()
            self.web_view.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            layout.addWidget(self.web_view)
            self._show_empty_state()
        else:
            fallback = QLabel(
                "⚠️ WebEngine not available.\n\n"
                "Install PyQt6-WebEngine:\n"
                "pip install PyQt6-WebEngine"
            )
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet("color: #8b949e; font-size: 12px;")
            layout.addWidget(fallback)

        # ── Stats Bar ────────────────────────────────────────────────────
        stats_bar = QFrame()
        stats_bar.setObjectName("statsBar")
        stats_bar.setFixedHeight(28)
        stats_layout = QHBoxLayout(stats_bar)
        stats_layout.setContentsMargins(12, 0, 12, 0)

        self.stats_label = QLabel("No project loaded")
        self.stats_label.setStyleSheet("color: #484f58; font-size: 10px;")
        stats_layout.addWidget(self.stats_label)
        layout.addWidget(stats_bar)

    def set_analyzer(self, analyzer):
        """Set the dependency analyzer instance."""
        self._analyzer = analyzer
        stats = analyzer.get_stats()
        self.stats_label.setText(
            f"Nodes: {stats['nodes']}  |  Edges: {stats['edges']}  |  "
            f"Symbols: {stats['symbols']}"
        )

    def show_file_graph(self, file_path: str):
        """Show dependency graph for a specific file."""
        if not self._analyzer or not HAS_WEBENGINE:
            return
        self._current_node = file_path
        subgraph = self._analyzer.get_file_subgraph(file_path)
        if subgraph.number_of_nodes() == 0:
            self._show_empty_state(f"No dependencies found for\n{os.path.basename(file_path)}")
            return
        html = build_pyvis_html(subgraph, f"File: {os.path.basename(file_path)}")
        self.web_view.setHtml(html, QUrl("about:blank"))

    def show_function_graph(self, func_name: str, file_path: str):
        """Show dependency graph centered on a function."""
        if not self._analyzer or not HAS_WEBENGINE:
            return
        node_id = f"{file_path}::{func_name}"
        subgraph = self._analyzer.get_subgraph_for_node(node_id, depth=2)
        if subgraph.number_of_nodes() == 0:
            self._show_empty_state(f"No graph data for\n{func_name}()")
            return
        html = build_pyvis_html(subgraph, f"Function: {func_name}()")
        self.web_view.setHtml(html, QUrl("about:blank"))

    def show_full_graph(self):
        """Show the complete project dependency graph."""
        if not self._analyzer or not HAS_WEBENGINE:
            return
        graph = self._analyzer.graph
        if graph.number_of_nodes() == 0:
            self._show_empty_state("No graph data available")
            return
        # Limit to avoid overload
        if graph.number_of_nodes() > 200:
            nodes = sorted(graph.degree(), key=lambda x: x[1], reverse=True)[:150]
            node_ids = [n[0] for n in nodes]
            graph = graph.subgraph(node_ids).copy()
        html = build_pyvis_html(graph, "Full Project Graph")
        self.web_view.setHtml(html, QUrl("about:blank"))

    def _show_empty_state(self, message: str = "Select a file or function to view its dependency graph"):
        if not HAS_WEBENGINE:
            return
        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
body {{ background:#0d1117; display:flex; justify-content:center; align-items:center;
       height:100vh; margin:0; font-family:'Segoe UI',sans-serif; color:#484f58; }}
.msg {{ text-align:center; }}
.icon {{ font-size:48px; margin-bottom:16px; }}
p {{ font-size:13px; line-height:1.6; }}
</style>
</head>
<body>
<div class="msg">
  <div class="icon">🕸</div>
  <p>{message}</p>
</div>
</body>
</html>"""
        self.web_view.setHtml(html, QUrl("about:blank"))

    def _on_scope_changed(self, scope: str):
        if scope == "Full Project":
            self.show_full_graph()
        elif self._current_node:
            self.show_file_graph(self._current_node)

    def _refresh(self):
        if self.scope_combo.currentText() == "Full Project":
            self.show_full_graph()
        elif self._current_node:
            self.show_file_graph(self._current_node)
