from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Symbol:
    id: str                 # stable id: "<rel_path>::<qualname>"
    name: str
    qualname: str           # e.g. "Detector.predict"
    kind: str               # "function" | "method" | "class"
    file: str               # repo-relative path
    start_line: int
    end_line: int
    docstring: str = ""
    code: str = ""
    calls: list[str] = field(default_factory=list)   # callee base-names found in body
    parent: Optional[str] = None                      # enclosing class qualname
    complexity: int = 0                               # cyclomatic-ish (functions/methods)
    bases: list[str] = field(default_factory=list)    # base classes (inheritance)
    decorators: list[str] = field(default_factory=list)  # decorator names (framework registration)
    call_sites: list = field(default_factory=list)       # (kind, name): bare|self|attr receiver


@dataclass
class ParsedFile:
    file: str
    language: str
    imports: list[str] = field(default_factory=list)
    import_targets: list[str] = field(default_factory=list)  # from-import submodule candidates (graph only)
    symbols: list[Symbol] = field(default_factory=list)
    loc: int = 0
    text: str = ""
    error: str = ""
    exports: list[str] = field(default_factory=list)  # names in module __all__
    has_main: bool = False                            # real `if __name__ == '__main__':` guard


@dataclass
class RepoMeta:
    name: str
    path: str
    commit: str
    branch: str = ""
    is_git: bool = False
    n_commits: int = 0
