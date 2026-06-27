"""
cache_manager.py — Persists analysis results to a local .kyp_cache folder.
Handles serialization, file modification checking, and cache invalidation.
"""
import os
import json
import hashlib
import time
from pathlib import Path
from typing import Optional, Any


class CacheManager:
    """
    Manages a local cache folder (.kyp_cache) inside the project root.
    Stores: file tree, parsed symbols, dependency graph, AI explanations.
    """

    CACHE_DIR_NAME = ".kyp_cache"
    CACHE_VERSION = "1.0"

    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.cache_dir = self.project_root / self.CACHE_DIR_NAME
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        """Create cache dir if it doesn't exist."""
        self.cache_dir.mkdir(exist_ok=True)
        # Create a .gitignore to exclude from git
        gitignore = self.cache_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n")

    def _cache_path(self, key: str) -> Path:
        """Get the file path for a cache key."""
        safe_key = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / f"{safe_key}.json"

    def get(self, key: str) -> Optional[Any]:
        """Retrieve a cached value. Returns None if not found or expired."""
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != self.CACHE_VERSION:
                return None
            return data.get("value")
        except (json.JSONDecodeError, IOError):
            return None

    def set(self, key: str, value: Any):
        """Store a value in the cache."""
        path = self._cache_path(key)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "version": self.CACHE_VERSION,
                    "key": key,
                    "timestamp": time.time(),
                    "value": value,
                }, f, indent=2, default=str)
        except (IOError, TypeError):
            pass

    def get_file_tree(self) -> Optional[dict]:
        return self.get("file_tree")

    def set_file_tree(self, tree_dict: dict):
        self.set("file_tree", tree_dict)

    def get_parsed_symbols(self, file_path: str) -> Optional[dict]:
        """Get cached parse result for a file, checking if file has changed."""
        key = f"parse::{file_path}"
        cached = self.get(key)
        if cached is None:
            return None
        # Check if file was modified after caching
        try:
            mtime = os.path.getmtime(file_path)
            if mtime > cached.get("cached_at", 0):
                return None  # File changed, cache invalid
        except OSError:
            return None
        return cached.get("data")

    def set_parsed_symbols(self, file_path: str, data: dict):
        key = f"parse::{file_path}"
        try:
            mtime = os.path.getmtime(file_path)
        except OSError:
            mtime = 0
        self.set(key, {"cached_at": mtime, "data": data})

    def get_ai_explanation(self, explanation_key: str) -> Optional[str]:
        return self.get(f"ai::{explanation_key}")

    def set_ai_explanation(self, explanation_key: str, text: str):
        self.set(f"ai::{explanation_key}", text)

    def get_dependency_graph(self) -> Optional[dict]:
        return self.get("dep_graph")

    def set_dependency_graph(self, graph_dict: dict):
        self.set("dep_graph", graph_dict)

    def clear(self):
        """Clear all cached data."""
        for f in self.cache_dir.glob("*.json"):
            try:
                f.unlink()
            except OSError:
                pass

    def get_size_mb(self) -> float:
        """Get total cache size in MB."""
        total = sum(
            f.stat().st_size for f in self.cache_dir.glob("*.json")
            if f.exists()
        )
        return total / (1024 * 1024)

    def get_stats(self) -> dict:
        """Return cache statistics."""
        files = list(self.cache_dir.glob("*.json"))
        return {
            "entries": len(files),
            "size_mb": round(self.get_size_mb(), 2),
            "cache_dir": str(self.cache_dir),
        }
