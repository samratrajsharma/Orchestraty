from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

# The version of what the engine computes: parsed files, fingerprints and edges. It is part of
# every cache key and cache file name, so an entry written by another version is never
# served. Bump it whenever parse output, fingerprints or edges change.
ENGINE_SCHEMA = 5


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
    call_sites: list = field(default_factory=list)       # (kind, name, attrs, line, ref[, args]): see parsing
    base_refs: list = field(default_factory=list)        # how each base class name is bound (classes)
    signature: dict = field(default_factory=dict)        # captured signature (Python): see parsing
    sig_hash: str = ""                                   # hash of the signature
    body_hash: str = ""                                  # hash of the normalised body
    overloaded: bool = False                             # has typing.overload stubs


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
    module_hash: str = ""                             # hash of the code outside any definition
    bindings: dict = field(default_factory=dict)      # module-level import bindings: name -> binding
    star_imports: list[str] = field(default_factory=list)   # Python: modules imported with *
    assigned: list[str] = field(default_factory=list)  # Python: other names assigned at module level
    reexports: dict = field(default_factory=dict)     # JS/TS: export { a } from ... -> binding
    star_reexports: list[str] = field(default_factory=list)  # JS/TS: export * from ...
    default_export: str = ""                          # JS/TS: the name exported as default
    routes: dict = field(default_factory=dict)        # Python: HTTP route facts (see routes.file_facts)


@dataclass
class RepoMeta:
    name: str
    path: str
    commit: str
    branch: str = ""
    is_git: bool = False
    n_commits: int = 0
