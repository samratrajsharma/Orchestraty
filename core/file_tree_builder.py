"""
file_tree_builder.py — Builds a hierarchical tree model of a project's files.
Detects language per file and provides statistics.
"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional
from core.project_loader import ProjectLoader


# Language extension map
LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".c": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".h": "C/C++ Header",
    ".hpp": "C++ Header",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".scala": "Scala",
    ".r": "R",
    ".R": "R",
    ".dart": "Dart",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "SASS",
    ".less": "LESS",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
    ".sh": "Shell",
    ".bash": "Bash",
    ".zsh": "ZSH",
    ".bat": "Batch",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".md": "Markdown",
    ".txt": "Text",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".lua": "Lua",
    ".pl": "Perl",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".fs": "F#",
    ".fsx": "F#",
    ".clj": "Clojure",
    ".groovy": "Groovy",
}

# Languages that can be parsed for functions/classes
PARSEABLE_LANGUAGES = {
    "Python", "JavaScript", "TypeScript", "Java", "C", "C++",
    "C#", "Go", "Rust", "Ruby", "PHP", "Swift", "Kotlin",
}


@dataclass
class TreeNode:
    """Represents a file or folder in the project tree."""
    name: str
    path: str
    is_dir: bool
    language: Optional[str] = None
    size: int = 0
    children: List["TreeNode"] = field(default_factory=list)
    parent: Optional["TreeNode"] = None

    @property
    def is_parseable(self) -> bool:
        return self.language in PARSEABLE_LANGUAGES

    @property
    def extension(self) -> str:
        return Path(self.path).suffix.lower()

    @property
    def relative_path(self) -> str:
        return self.path

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "is_dir": self.is_dir,
            "language": self.language,
            "size": self.size,
            "children": [c.to_dict() for c in self.children],
        }

    @staticmethod
    def from_dict(d: dict, parent=None) -> "TreeNode":
        node = TreeNode(
            name=d["name"],
            path=d["path"],
            is_dir=d["is_dir"],
            language=d.get("language"),
            size=d.get("size", 0),
            parent=parent,
        )
        node.children = [TreeNode.from_dict(c, node) for c in d.get("children", [])]
        return node


class FileTreeBuilder:
    """Builds a TreeNode hierarchy from a project root directory."""

    def __init__(self, project_root: str, max_files: int = 2000):
        self.project_root = Path(project_root)
        self.max_files = max_files
        self._file_count = 0

    def build(self) -> TreeNode:
        """Build and return the root TreeNode."""
        self._file_count = 0
        root = self._build_node(self.project_root, parent=None)
        return root

    def _build_node(self, path: Path, parent: Optional[TreeNode]) -> Optional[TreeNode]:
        """Recursively build TreeNode for a path."""
        if self._file_count >= self.max_files:
            return None

        if ProjectLoader.should_ignore(path):
            return None

        node = TreeNode(
            name=path.name,
            path=str(path),
            is_dir=path.is_dir(),
            parent=parent,
        )

        if path.is_file():
            self._file_count += 1
            node.language = LANGUAGE_MAP.get(path.suffix.lower())
            try:
                node.size = path.stat().st_size
            except OSError:
                node.size = 0

        elif path.is_dir():
            try:
                children = sorted(
                    path.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name.lower())
                )
                for child in children:
                    child_node = self._build_node(child, parent=node)
                    if child_node is not None:
                        node.children.append(child_node)
            except PermissionError:
                pass

        return node

    def get_all_files(self, root: Optional[TreeNode] = None) -> List[TreeNode]:
        """Flatten tree to get all file nodes."""
        if root is None:
            root = self.build()
        files = []
        self._collect_files(root, files)
        return files

    def _collect_files(self, node: TreeNode, files: List[TreeNode]):
        if not node.is_dir:
            files.append(node)
        for child in node.children:
            self._collect_files(child, files)

    def get_stats(self, root: TreeNode) -> dict:
        """Get project statistics."""
        files = []
        self._collect_files(root, files)

        lang_count = {}
        total_size = 0
        for f in files:
            if f.language:
                lang_count[f.language] = lang_count.get(f.language, 0) + 1
            total_size += f.size

        return {
            "total_files": len(files),
            "total_dirs": self._count_dirs(root),
            "languages": lang_count,
            "total_size": total_size,
            "primary_language": max(lang_count, key=lang_count.get) if lang_count else "Unknown",
        }

    def _count_dirs(self, node: TreeNode) -> int:
        count = 1 if node.is_dir else 0
        for child in node.children:
            count += self._count_dirs(child)
        return count
