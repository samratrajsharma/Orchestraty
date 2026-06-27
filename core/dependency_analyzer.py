"""
dependency_analyzer.py — Builds a call/dependency graph using NetworkX.
Analyzes function calls across files to produce a directed graph.
"""
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import networkx as nx

from core.code_parser import ParsedFile, Symbol


class DependencyAnalyzer:
    """
    Builds a directed graph of function/file dependencies.
    Nodes = files or functions.
    Edges = imports, calls, or references.
    """

    def __init__(self):
        self.graph: nx.DiGraph = nx.DiGraph()
        self._parsed_files: Dict[str, ParsedFile] = {}
        self._symbol_index: Dict[str, Tuple[str, Symbol]] = {}  # name -> (file_path, symbol)

    def add_file(self, parsed: ParsedFile):
        """Add a parsed file to the analyzer."""
        self._parsed_files[parsed.path] = parsed

        # Add file node
        rel_path = os.path.basename(parsed.path)
        self.graph.add_node(
            parsed.path,
            label=rel_path,
            kind="file",
            language=parsed.language,
            line_count=parsed.line_count,
        )

        # Add symbol nodes
        for symbol in parsed.symbols:
            node_id = f"{parsed.path}::{symbol.name}"
            self.graph.add_node(
                node_id,
                label=symbol.name,
                kind=symbol.kind,
                file=parsed.path,
                line_start=symbol.line_start,
            )
            # File -> Symbol edge
            self.graph.add_edge(parsed.path, node_id, relation="contains")

            # Index symbol by name
            self._symbol_index[symbol.name] = (parsed.path, symbol)

    def build_call_edges(self):
        """Resolve function calls and add edges between symbols."""
        for file_path, parsed in self._parsed_files.items():
            for symbol in parsed.get_functions():
                caller_id = f"{file_path}::{symbol.name}"
                for call_name in symbol.calls:
                    if call_name in self._symbol_index:
                        callee_path, callee_sym = self._symbol_index[call_name]
                        callee_id = f"{callee_path}::{call_name}"
                        if caller_id != callee_id:
                            self.graph.add_edge(caller_id, callee_id, relation="calls")

    def build_import_edges(self):
        """Add import-based edges between files."""
        for file_path, parsed in self._parsed_files.items():
            for imp in parsed.imports:
                # Try to resolve which file is being imported
                for other_path in self._parsed_files:
                    other_name = Path(other_path).stem
                    if other_name in imp and other_path != file_path:
                        self.graph.add_edge(file_path, other_path, relation="imports")

    def get_neighbors(self, node_id: str) -> List[str]:
        """Get direct neighbors of a node."""
        if node_id not in self.graph:
            return []
        return list(self.graph.neighbors(node_id))

    def get_callers(self, func_name: str, file_path: str) -> List[str]:
        """Get all functions that call the given function."""
        node_id = f"{file_path}::{func_name}"
        if node_id not in self.graph:
            return []
        return [
            n for n in self.graph.predecessors(node_id)
            if self.graph[n][node_id].get("relation") == "calls"
        ]

    def get_callees(self, func_name: str, file_path: str) -> List[str]:
        """Get all functions called by the given function."""
        node_id = f"{file_path}::{func_name}"
        if node_id not in self.graph:
            return []
        return [
            n for n in self.graph.successors(node_id)
            if self.graph[node_id][n].get("relation") == "calls"
        ]

    def get_subgraph_for_node(self, node_id: str, depth: int = 2) -> nx.DiGraph:
        """Get a subgraph centered on a node up to `depth` hops away."""
        if node_id not in self.graph:
            return nx.DiGraph()
        nodes = {node_id}
        frontier = {node_id}
        for _ in range(depth):
            next_frontier = set()
            for n in frontier:
                next_frontier.update(self.graph.predecessors(n))
                next_frontier.update(self.graph.successors(n))
            nodes.update(next_frontier)
            frontier = next_frontier
        return self.graph.subgraph(nodes).copy()

    def get_file_subgraph(self, file_path: str) -> nx.DiGraph:
        """Get the subgraph for a specific file (its symbols + their dependencies)."""
        if file_path not in self.graph:
            return nx.DiGraph()
        # Get all symbols in this file
        file_nodes = {file_path}
        for n in self.graph.successors(file_path):
            if self.graph[file_path][n].get("relation") == "contains":
                file_nodes.add(n)
                # Add their direct call neighbors
                for callee in self.graph.successors(n):
                    file_nodes.add(callee)
                for caller in self.graph.predecessors(n):
                    file_nodes.add(caller)
        return self.graph.subgraph(file_nodes).copy()

    def get_most_connected(self, top_n: int = 10) -> List[Tuple[str, int]]:
        """Return top N most connected nodes by degree."""
        degrees = sorted(self.graph.degree(), key=lambda x: x[1], reverse=True)
        return degrees[:top_n]

    def get_stats(self) -> dict:
        """Return graph statistics."""
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "files": len(self._parsed_files),
            "symbols": len(self._symbol_index),
            "is_connected": nx.is_weakly_connected(self.graph) if self.graph.number_of_nodes() > 0 else False,
        }

    def to_dict(self) -> dict:
        """Serialize graph to dict for caching."""
        return {
            "nodes": [
                {"id": n, **self.graph.nodes[n]}
                for n in self.graph.nodes
            ],
            "edges": [
                {"source": u, "target": v, **self.graph[u][v]}
                for u, v in self.graph.edges
            ],
        }
