"""
main_window.py — Root PyQt6 QMainWindow. Orchestrates all panels and core modules.
Three-panel layout: File Tree | Code Viewer + Explanation | Dependency Graph
"""
import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QStackedWidget, QStatusBar, QMenuBar,
    QMenu, QProgressBar, QLabel, QFileDialog, QApplication,
    QFrame, QPushButton, QToolBar, QTabWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QRunnable, QThreadPool, QTimer
from PyQt6.QtGui import QFont, QAction, QKeySequence

from ui.welcome_screen import WelcomeScreen
from ui.file_tree_panel import FileTreePanel
from ui.code_viewer import CodeViewerPanel
from ui.explanation_panel import ExplanationPanel
from ui.dependency_graph_panel import DependencyGraphPanel
from ui.dependency_window import DependencyWindow
from ui.mindmap_widget import MindMapWidget
from ui.settings_dialog import SettingsDialog
from ui.function_popup import FunctionPopup

from core.project_loader import ProjectLoader
from core.file_tree_builder import FileTreeBuilder, TreeNode
from core.code_parser import CodeParser
from core.dependency_analyzer import DependencyAnalyzer
from core.ai_explainer import AIExplainer
from core.cache_manager import CacheManager


class AnalysisWorker(QRunnable):
    """Background worker that parses all project files."""

    class Signals(QObject):
        progress = pyqtSignal(str, int)   # (message, percent)
        finished = pyqtSignal(object, object)  # (parsed_files dict, analyzer)
        error = pyqtSignal(str)

    def __init__(self, file_nodes: list, cache_manager=None):
        super().__init__()
        self.file_nodes = file_nodes
        self.cache_manager = cache_manager
        self.signals = AnalysisWorker.Signals()

    def run(self):
        try:
            parser = CodeParser()
            analyzer = DependencyAnalyzer()
            parsed_files = {}
            total = len(self.file_nodes)

            for i, node in enumerate(self.file_nodes):
                if not node.language:
                    continue
                pct = int((i / max(total, 1)) * 80)
                self.signals.progress.emit(
                    f"Parsing {node.name}...", pct)

                # Check cache
                cached = None
                if self.cache_manager:
                    cached = self.cache_manager.get_parsed_symbols(node.path)

                if cached:
                    from core.code_parser import ParsedFile, Symbol
                    parsed = ParsedFile(
                        path=node.path,
                        language=node.language,
                        content="",
                        line_count=cached.get("line_count", 0),
                    )
                else:
                    parsed = parser.parse_file(node.path, node.language)
                    if parsed and self.cache_manager:
                        self.cache_manager.set_parsed_symbols(
                            node.path, parsed.to_dict())

                if parsed:
                    parsed_files[node.path] = parsed
                    analyzer.add_file(parsed)

            self.signals.progress.emit("Building dependency graph...", 85)
            analyzer.build_call_edges()
            analyzer.build_import_edges()

            self.signals.progress.emit("Analysis complete!", 100)
            self.signals.finished.emit(parsed_files, analyzer)

        except Exception as e:
            self.signals.error.emit(str(e))


class MainWindow(QMainWindow):
    """
    Root application window with three-panel layout.
    Coordinates all UI and core components.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Know Your Projects")
        self.setMinimumSize(1200, 700)
        self.resize(1400, 850)

        # ── State ─────────────────────────────────────────────────────────
        self._project_root: str = ""
        self._project_name: str = ""
        self._parsed_files: dict = {}
        self._tree_root: TreeNode | None = None
        self._cache_manager: CacheManager | None = None
        self._analyzer: DependencyAnalyzer | None = None

        # ── Core Components ───────────────────────────────────────────────
        self._loader = ProjectLoader()
        self._loader.finished.connect(self._on_project_loaded)
        self._loader.error.connect(self._on_load_error)

        api_key = SettingsDialog.get_api_key()
        self._explainer = AIExplainer(api_key)
        self._explainer.explanation_chunk.connect(self._on_explanation_chunk)
        self._explainer.explanation_done.connect(self._on_explanation_done)
        self._explainer.explanation_error.connect(self._on_explanation_error)

        self._thread_pool = QThreadPool.globalInstance()

        # ── Build UI ──────────────────────────────────────────────────────
        self._init_menu()
        self._init_toolbar()
        self._init_central_widget()
        self._init_status_bar()

    # ═══════════════════════════════════════════════════════════════════════
    # UI CONSTRUCTION
    # ═══════════════════════════════════════════════════════════════════════

    def _init_menu(self):
        menubar = self.menuBar()
        menubar.setObjectName("mainMenuBar")

        # File menu
        file_menu = menubar.addMenu("&File")
        open_folder_act = QAction("📂 Open Folder...", self)
        open_folder_act.setShortcut(QKeySequence("Ctrl+O"))
        open_folder_act.triggered.connect(self._browse_folder)
        file_menu.addAction(open_folder_act)

        open_github_act = QAction("🔗 Open GitHub URL...", self)
        open_github_act.setShortcut(QKeySequence("Ctrl+G"))
        open_github_act.triggered.connect(self._prompt_github_url)
        file_menu.addAction(open_github_act)

        file_menu.addSeparator()

        close_act = QAction("❌ Close Project", self)
        close_act.triggered.connect(self._close_project)
        file_menu.addAction(close_act)

        file_menu.addSeparator()

        exit_act = QAction("Quit", self)
        exit_act.setShortcut(QKeySequence("Ctrl+Q"))
        exit_act.triggered.connect(QApplication.quit)
        file_menu.addAction(exit_act)

        # View menu
        view_menu = menubar.addMenu("&View")
        self.toggle_tree_act = QAction("📁 File Tree", self, checkable=True, checked=True)
        self.toggle_tree_act.triggered.connect(self._toggle_tree_panel)
        view_menu.addAction(self.toggle_tree_act)

        self.toggle_graph_act = QAction("🕸 Graph Panel", self, checkable=True, checked=True)
        self.toggle_graph_act.triggered.connect(self._toggle_graph_panel)
        view_menu.addAction(self.toggle_graph_act)

        # Tools menu
        tools_menu = menubar.addMenu("&Tools")
        settings_act = QAction("⚙️ Settings...", self)
        settings_act.setShortcut(QKeySequence("Ctrl+,"))
        settings_act.triggered.connect(self._open_settings)
        tools_menu.addAction(settings_act)

        clear_cache_act = QAction("🗑 Clear Cache", self)
        clear_cache_act.triggered.connect(self._clear_cache)
        tools_menu.addAction(clear_cache_act)

    def _init_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        # Left side: project controls
        open_btn = QPushButton("📂 Open Folder")
        open_btn.setObjectName("toolbarBtn")
        open_btn.clicked.connect(self._browse_folder)
        toolbar.addWidget(open_btn)

        github_btn = QPushButton("🔗 GitHub URL")
        github_btn.setObjectName("toolbarBtn")
        github_btn.clicked.connect(self._prompt_github_url)
        toolbar.addWidget(github_btn)

        # Spacer
        spacer = QWidget()
        spacer.setSizePolicy(
            spacer.sizePolicy().horizontalPolicy(),
            spacer.sizePolicy().verticalPolicy()
        )
        from PyQt6.QtWidgets import QSizePolicy
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # Project name label
        self.project_name_label = QLabel("")
        self.project_name_label.setObjectName("projectNameLabel")
        self.project_name_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        toolbar.addWidget(self.project_name_label)

        # Right side: settings
        spacer2 = QWidget()
        spacer2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer2)

        settings_btn = QPushButton("⚙️")
        settings_btn.setObjectName("toolbarIconBtn")
        settings_btn.setFixedSize(36, 36)
        settings_btn.setToolTip("Settings")
        settings_btn.clicked.connect(self._open_settings)
        toolbar.addWidget(settings_btn)

        self.addToolBar(toolbar)

    def _init_central_widget(self):
        # Stack: welcome screen ↔ main layout
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # ── Welcome Screen ─────────────────────────────────────────────
        self.welcome = WelcomeScreen()
        self.welcome.open_folder_requested.connect(self._load_folder)
        self.welcome.open_github_requested.connect(self._load_github)
        self.stack.addWidget(self.welcome)

        # ── Main Layout ────────────────────────────────────────────────
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Outer splitter: Tree | Center | Graph
        self.outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.outer_splitter.setHandleWidth(2)
        self.outer_splitter.setObjectName("outerSplitter")

        # Left panel: File Tree + Mind Map tabs
        self.left_tabs = QTabWidget()
        self.left_tabs.setObjectName("leftTabs")
        self.left_tabs.setFixedWidth(280)

        self.file_tree_panel = FileTreePanel()
        self.file_tree_panel.file_selected.connect(self._on_file_selected)
        self.left_tabs.addTab(self.file_tree_panel, "📁 Files")

        self.mindmap_widget = MindMapWidget()
        self.left_tabs.addTab(self.mindmap_widget, "🗺️ Map")

        self.outer_splitter.addWidget(self.left_tabs)

        # Center panel: Code Viewer + Explanation splitter
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        self.center_splitter = QSplitter(Qt.Orientation.Vertical)
        self.center_splitter.setHandleWidth(4)
        self.center_splitter.setObjectName("centerSplitter")

        self.code_viewer = CodeViewerPanel()
        self.code_viewer.function_clicked.connect(self._on_function_clicked)
        self.center_splitter.addWidget(self.code_viewer)

        self.explanation_panel = ExplanationPanel()
        self.center_splitter.addWidget(self.explanation_panel)

        self.center_splitter.setSizes([500, 300])
        center_layout.addWidget(self.center_splitter)
        self.outer_splitter.addWidget(center_widget)

        # Right panel: Dependency graph
        self.dep_graph_panel = DependencyGraphPanel()
        self.dep_graph_panel.setMinimumWidth(280)
        self.dep_graph_panel.open_fullscreen.connect(self._open_dependency_window)
        self.outer_splitter.addWidget(self.dep_graph_panel)

        self.outer_splitter.setSizes([280, 700, 320])

        main_layout.addWidget(self.outer_splitter)
        self.stack.addWidget(main_widget)

        # Show welcome by default
        self.stack.setCurrentIndex(0)

    def _init_status_bar(self):
        self.status_bar = QStatusBar()
        self.status_bar.setObjectName("mainStatusBar")
        self.setStatusBar(self.status_bar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("mainProgress")
        self.progress_bar.setFixedWidth(200)
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label)

    # ═══════════════════════════════════════════════════════════════════════
    # PROJECT LOADING
    # ═══════════════════════════════════════════════════════════════════════

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Open Project Folder", os.path.expanduser("~")
        )
        if folder:
            self._load_folder(folder)

    def _prompt_github_url(self):
        from PyQt6.QtWidgets import QInputDialog
        url, ok = QInputDialog.getText(
            self, "Open GitHub Repository",
            "Enter GitHub URL:",
            text="https://github.com/"
        )
        if ok and url.strip():
            self._load_github(url.strip())

    def _load_folder(self, folder_path: str):
        self._set_loading(True, "Loading project...")
        self._loader.load_from_folder(folder_path)

    def _load_github(self, url: str):
        self._set_loading(True, "Cloning repository...")
        self._loader.load_from_github(url)

    def _on_project_loaded(self, project_name: str, project_root: str):
        """Called when a project folder is ready for analysis."""
        self._project_root = project_root
        self._project_name = project_name
        self.project_name_label.setText(f"📦 {project_name}")
        self.setWindowTitle(f"Know Your Projects — {project_name}")

        # Initialize cache
        if SettingsDialog.is_cache_enabled():
            self._cache_manager = CacheManager(project_root)
        else:
            self._cache_manager = None

        # Build file tree
        self._update_status("Building file tree...", 20)
        max_files = SettingsDialog.get_max_files()
        builder = FileTreeBuilder(project_root, max_files=max_files)
        self._tree_root = builder.build()
        stats = builder.get_stats(self._tree_root)
        all_files = builder.get_all_files(self._tree_root)

        # Load tree panel
        self.file_tree_panel.load_tree(self._tree_root, stats)
        self.mindmap_widget.load_tree(self._tree_root)

        # Switch to main view
        self.stack.setCurrentIndex(1)

        # Start background analysis
        self._update_status("Analyzing code structure...", 30)
        worker = AnalysisWorker(all_files, self._cache_manager)
        worker.signals.progress.connect(self._update_status)
        worker.signals.finished.connect(self._on_analysis_done)
        worker.signals.error.connect(self._on_load_error)
        self._thread_pool.start(worker)

        # Show project overview explanation
        self._explainer.set_api_key(SettingsDialog.get_api_key())
        file_list = "\n".join(
            f.path for f in all_files[:30]
        )
        self.explanation_panel.start_streaming(f"📦 {project_name}")
        self._explainer.explain_project(
            project_name=project_name,
            language=stats.get("primary_language", "Unknown"),
            total_files=stats.get("total_files", 0),
            file_structure=file_list,
        )

    def _on_analysis_done(self, parsed_files: dict, analyzer: DependencyAnalyzer):
        """Called when background analysis is complete."""
        self._parsed_files = parsed_files
        self._analyzer = analyzer
        self.dep_graph_panel.set_analyzer(analyzer)
        self.dep_graph_panel.show_full_graph()
        self._set_loading(False)
        self._update_status(
            f"✅ Loaded {len(parsed_files)} files | "
            f"{analyzer.get_stats()['symbols']} symbols analyzed", 100
        )
        QTimer.singleShot(3000, lambda: self.progress_bar.setVisible(False))

    def _on_load_error(self, error_msg: str):
        self._set_loading(False)
        self._update_status(f"❌ Error: {error_msg}", 0)
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(self, "Load Error", error_msg)

    # ═══════════════════════════════════════════════════════════════════════
    # FILE / FUNCTION SELECTION
    # ═══════════════════════════════════════════════════════════════════════

    def _on_file_selected(self, file_path: str, tree_node):
        """User clicked a file in the tree."""
        # Load code viewer
        parsed = self._parsed_files.get(file_path)
        self.code_viewer.load_file(file_path, parsed)

        # Update dependency graph
        if self._analyzer:
            self.dep_graph_panel.show_file_graph(file_path)

        # Request AI explanation
        language = getattr(tree_node, "language", None) or "Unknown"
        if parsed:
            language = parsed.language

        self.explanation_panel.start_streaming(
            f"📄 {os.path.basename(file_path)}"
        )
        self._explainer.set_api_key(SettingsDialog.get_api_key())

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except IOError:
            content = ""

        self._explainer.explain_file(
            file_path=file_path,
            language=language,
            line_count=content.count("\n") + 1,
            code_snippet=content,
        )
        self._update_status(f"Loaded: {os.path.basename(file_path)}", -1)

    def _on_function_clicked(self, func_name: str, line_no: int):
        """User clicked a function in the code viewer."""
        current_file = self.code_viewer.get_current_file()
        current_parsed = self.code_viewer.get_current_parsed()
        if not current_parsed:
            return

        # Find the symbol
        symbol = next(
            (s for s in current_parsed.symbols if s.name == func_name),
            None
        )
        if not symbol:
            return

        # Get callers/callees from analyzer
        callers = []
        callees = []
        if self._analyzer:
            callers = self._analyzer.get_callers(func_name, current_file)
            callees = self._analyzer.get_callees(func_name, current_file)
            # Clean up node IDs to readable names
            callers = [c.split("::")[-1] for c in callers]
            callees = [c.split("::")[-1] for c in callees]

        # Update graph to focus on this function
        if self._analyzer:
            self.dep_graph_panel.show_function_graph(func_name, current_file)

        # Show popup
        popup = FunctionPopup(
            func_name=func_name,
            file_path=current_file,
            func_code=symbol.code,
            language=current_parsed.language,
            callers=callers,
            callees=callees,
            ai_explainer=AIExplainer(SettingsDialog.get_api_key()),
            parent=self,
        )
        popup.show_in_graph.connect(
            lambda fn, fp: self.dep_graph_panel.show_function_graph(fn, fp))
        popup.exec()

    # ═══════════════════════════════════════════════════════════════════════
    # AI EXPLANATION HANDLERS
    # ═══════════════════════════════════════════════════════════════════════

    def _on_explanation_chunk(self, chunk: str):
        self.explanation_panel.append_chunk(chunk)

    def _on_explanation_done(self, full_text: str):
        self.explanation_panel.finish_streaming(full_text)

    def _on_explanation_error(self, error: str):
        self.explanation_panel.show_error(error)

    # ═══════════════════════════════════════════════════════════════════════
    # UI HELPERS
    # ═══════════════════════════════════════════════════════════════════════

    def _set_loading(self, loading: bool, message: str = ""):
        self.progress_bar.setVisible(loading)
        if loading:
            self.progress_bar.setRange(0, 0)  # Indeterminate
            self.status_label.setText(message)
        else:
            self.progress_bar.setRange(0, 100)

    def _update_status(self, message: str, percent: int = -1):
        self.status_label.setText(message)
        if percent >= 0:
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percent)

    def _toggle_tree_panel(self, checked: bool):
        self.left_tabs.setVisible(checked)

    def _toggle_graph_panel(self, checked: bool):
        self.dep_graph_panel.setVisible(checked)

    def _open_settings(self):
        dialog = SettingsDialog(self)
        dialog.api_key_changed.connect(self._explainer.set_api_key)
        dialog.theme_changed.connect(self._apply_theme)
        dialog.exec()

    def _apply_theme(self, theme: str):
        if "Light" in theme:
            theme_file = "assets/styles/light_theme.qss"
        else:
            theme_file = "assets/styles/dark_theme.qss"
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), theme_file)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                QApplication.instance().setStyleSheet(f.read())

    def _clear_cache(self):
        if self._cache_manager:
            self._cache_manager.clear()
            self._update_status("Cache cleared ✅", -1)

    def _close_project(self):
        self._project_root = ""
        self._project_name = ""
        self._parsed_files = {}
        self._tree_root = None
        self._analyzer = None
        self.file_tree_panel.clear()
        self.code_viewer.clear()
        self.explanation_panel.set_static_text("## Load a project to get started")
        self.project_name_label.setText("")
        self.setWindowTitle("Know Your Projects")
        self.stack.setCurrentIndex(0)

    def _open_dependency_window(self):
        """Open the full-screen dependency graph window."""
        if not self._analyzer:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "No Project",
                "Please open a project first to view the dependency graph."
            )
            return
        self._dep_window = DependencyWindow(
            analyzer=self._analyzer,
            project_name=self._project_name,
            parent=self,
        )
        self._dep_window.show()
        self._dep_window.raise_()
        self._dep_window.activateWindow()

    def closeEvent(self, event):
        """Clean up temp directories on close."""
        self._loader.cleanup()
        event.accept()
