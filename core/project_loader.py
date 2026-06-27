"""
project_loader.py — Loads a project from a local folder or a GitHub URL.
Emits progress signals for the UI to display.
"""
import os
import re
import shutil
import tempfile
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal, QThread


class ProjectLoader(QObject):
    """Loads projects from disk or GitHub."""

    progress = pyqtSignal(str)          # Status message
    progress_pct = pyqtSignal(int)      # 0-100 percent
    finished = pyqtSignal(str, str)     # (project_name, project_root_path)
    error = pyqtSignal(str)             # Error message

    IGNORE_DIRS = {
        ".git", ".svn", ".hg", "node_modules", "__pycache__",
        ".mypy_cache", ".pytest_cache", "venv", ".venv", "env",
        "dist", "build", ".next", ".nuxt", "target", "out",
        ".idea", ".vscode", "coverage", ".tox", "eggs", ".eggs",
        "buck-out", ".dart_tool", ".flutter-plugins",
    }

    IGNORE_FILES = {
        ".DS_Store", "Thumbs.db", ".gitignore", ".gitattributes",
        "package-lock.json", "yarn.lock", "poetry.lock", "Pipfile.lock",
    }

    IGNORE_EXTENSIONS = {
        ".pyc", ".pyo", ".class", ".o", ".obj", ".exe", ".dll", ".so",
        ".dylib", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
        ".mp4", ".mp3", ".wav", ".zip", ".tar", ".gz", ".rar",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx",
        ".min.js", ".min.css",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._temp_dirs = []

    def load_from_folder(self, folder_path: str):
        """Load from a local folder path."""
        path = Path(folder_path)
        if not path.exists():
            self.error.emit(f"Path does not exist: {folder_path}")
            return
        if not path.is_dir():
            self.error.emit(f"Path is not a directory: {folder_path}")
            return

        self.progress.emit(f"Loading project: {path.name}")
        self.progress_pct.emit(10)
        self.finished.emit(path.name, str(path))

    def load_from_github(self, url: str):
        """Clone a GitHub repository and load it."""
        try:
            import git
        except ImportError:
            self.error.emit("GitPython not installed. Run: pip install GitPython")
            return

        # Validate URL
        github_pattern = r"https?://github\.com/[\w\-\.]+/[\w\-\.]+"
        if not re.match(github_pattern, url):
            self.error.emit("Invalid GitHub URL. Expected: https://github.com/user/repo")
            return

        # Extract repo name
        repo_name = url.rstrip("/").split("/")[-1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]

        self.progress.emit(f"Cloning {repo_name} from GitHub...")
        self.progress_pct.emit(10)

        try:
            temp_dir = tempfile.mkdtemp(prefix="kyp_")
            self._temp_dirs.append(temp_dir)
            clone_path = os.path.join(temp_dir, repo_name)

            self.progress.emit("Downloading repository...")
            self.progress_pct.emit(30)

            git.Repo.clone_from(url, clone_path, depth=1)

            self.progress.emit("Repository cloned successfully!")
            self.progress_pct.emit(80)
            self.finished.emit(repo_name, clone_path)

        except Exception as e:
            self.error.emit(f"Failed to clone repository: {str(e)}")

    def cleanup(self):
        """Remove any temporary directories created during GitHub loads."""
        for temp_dir in self._temp_dirs:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        self._temp_dirs.clear()

    @staticmethod
    def should_ignore(path: Path) -> bool:
        """Returns True if this file/dir should be excluded from analysis."""
        name = path.name
        if name in ProjectLoader.IGNORE_DIRS:
            return True
        if name in ProjectLoader.IGNORE_FILES:
            return True
        suffix = path.suffix.lower()
        if suffix in ProjectLoader.IGNORE_EXTENSIONS:
            return True
        # Ignore hidden files/dirs (except .env)
        if name.startswith(".") and name != ".env":
            return True
        return False
