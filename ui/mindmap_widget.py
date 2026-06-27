"""
mindmap_widget.py — Interactive mind map visualization of the project structure.
Uses PyVis rendered in QWebEngineView.
"""
import os
import json
import tempfile
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal, QUrl
from PyQt6.QtGui import QFont

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

from core.file_tree_builder import TreeNode


# Language color fallback
LANG_COLORS_MM = {
    "Python": "#3572A5", "JavaScript": "#f1e05a", "TypeScript": "#2b7489",
    "Java": "#b07219", "C++": "#f34b7d", "C": "#555555", "C#": "#178600",
    "Go": "#00ADD8", "Rust": "#dea584", "Ruby": "#701516", "HTML": "#e34c26",
    "CSS": "#563d7c", "JSON": "#8b949e", "Markdown": "#083fa1",
}


def build_mindmap_html(root_node: TreeNode, max_nodes: int = 120) -> str:
    """Build an interactive mindmap HTML from a TreeNode tree."""
    nodes = []
    edges = []
    node_count = [0]

    def add_node(node: TreeNode, parent_id: str = None, level: int = 0):
        if node_count[0] >= max_nodes:
            return
        node_id = str(id(node))
        node_count[0] += 1

        if node.is_dir:
            color = "#1f6feb"
            shape = "box"
            label = f"📂 {node.name}"
            size = 25 if level == 0 else 18
        else:
            lang = node.language or "Other"
            color = LANG_COLORS_MM.get(lang, "#8b949e")
            shape = "dot"
            label = node.name
            size = 12

        nodes.append({
            "id": node_id,
            "label": label[:20],
            "color": color,
            "shape": shape,
            "size": size,
            "title": f"{node.language or 'Folder'}: {node.name}",
            "level": level,
        })

        if parent_id:
            edges.append({"from": parent_id, "to": node_id, "color": "#30363d"})

        for child in node.children[:20]:  # Limit per folder
            add_node(child, node_id, level + 1)

    add_node(root_node)

    nodes_js = json.dumps(nodes)
    edges_js = json.dumps(edges)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis-network/9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0d1117; overflow: hidden; width: 100vw; height: 100vh; }}
  #mindmap {{ width: 100%; height: 100%; }}
</style>
</head>
<body>
<div id="mindmap"></div>
<script>
var nodes = new vis.DataSet({nodes_js});
var edges = new vis.DataSet({edges_js});
var options = {{
  layout: {{
    hierarchical: {{
      enabled: true,
      direction: "UD",
      sortMethod: "directed",
      levelSeparation: 80,
      nodeSpacing: 120
    }}
  }},
  nodes: {{
    font: {{ color: "#e6edf3", size: 12, face: "Segoe UI" }},
    borderWidth: 2,
    shadow: true
  }},
  edges: {{
    smooth: {{ type: "cubicBezier" }},
    color: "#30363d",
    shadow: true
  }},
  physics: {{ enabled: false }},
  interaction: {{
    hover: true,
    navigationButtons: false,
    keyboard: true,
    zoomView: true
  }}
}};
var container = document.getElementById("mindmap");
var network = new vis.Network(container, {{nodes: nodes, edges: edges}}, options);
network.on("click", function(params) {{
  if (params.nodes.length > 0) {{
    var nodeId = params.nodes[0];
    var node = nodes.get(nodeId);
    if (window.qt_bridge) {{
      window.qt_bridge.nodeClicked(node.title);
    }}
  }}
}});
network.fit({{ animation: {{ duration: 500, easingFunction: "easeInOutQuad" }} }});
</script>
</body>
</html>"""


class MindMapWidget(QWidget):
    """
    Interactive mind map view of the project file structure.
    Embedded via QWebEngineView.
    """

    node_clicked = pyqtSignal(str)  # node title/path clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if HAS_WEBENGINE:
            self.web_view = QWebEngineView()
            self.web_view.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            layout.addWidget(self.web_view)
            self._show_empty()
        else:
            from PyQt6.QtWidgets import QLabel
            label = QLabel("Mind map requires PyQt6-WebEngine.\nInstall: pip install PyQt6-WebEngine")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #8b949e;")
            layout.addWidget(label)

    def load_tree(self, root_node: TreeNode):
        """Render the project tree as a mind map."""
        if not HAS_WEBENGINE:
            return
        html = build_mindmap_html(root_node)
        self.web_view.setHtml(html, QUrl("about:blank"))

    def _show_empty(self):
        if not HAS_WEBENGINE:
            return
        html = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body { background:#0d1117; display:flex; justify-content:center;
         align-items:center; height:100vh; margin:0;
         font-family:'Segoe UI',sans-serif; color:#484f58; text-align:center; }
</style></head>
<body>
  <div>
    <div style="font-size:48px;margin-bottom:16px;">🗺️</div>
    <p style="font-size:13px;">Load a project to see the mind map</p>
  </div>
</body></html>"""
        self.web_view.setHtml(html, QUrl("about:blank"))
