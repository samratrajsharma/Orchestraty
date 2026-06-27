"""
code_parser.py — Parses source files using regex-based extraction.
Extracts functions, classes, methods, and imports from source code.
Supports Python, JS/TS, Java, C/C++, Go, Rust, and more via fallback patterns.
"""
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class Symbol:
    """Represents a code symbol (function, class, method, etc.)."""
    name: str
    kind: str           # 'function', 'class', 'method', 'import'
    line_start: int
    line_end: int
    code: str           # The actual source snippet
    docstring: str = ""
    calls: List[str] = field(default_factory=list)   # Functions this symbol calls
    decorators: List[str] = field(default_factory=list)
    is_exported: bool = False


@dataclass
class ParsedFile:
    """Parsed representation of a source file."""
    path: str
    language: str
    content: str
    symbols: List[Symbol] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    exports: List[str] = field(default_factory=list)
    line_count: int = 0

    def get_functions(self) -> List[Symbol]:
        return [s for s in self.symbols if s.kind in ("function", "method")]

    def get_classes(self) -> List[Symbol]:
        return [s for s in self.symbols if s.kind == "class"]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "language": self.language,
            "line_count": self.line_count,
            "symbols": [
                {
                    "name": s.name, "kind": s.kind,
                    "line_start": s.line_start, "line_end": s.line_end,
                    "docstring": s.docstring, "calls": s.calls,
                }
                for s in self.symbols
            ],
            "imports": self.imports,
        }


class CodeParser:
    """
    Parses source files and extracts symbols using regex patterns.
    Provides a consistent interface regardless of language.
    """

    def parse_file(self, file_path: str, language: str) -> Optional[ParsedFile]:
        """Parse a single file and return its symbols."""
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except (IOError, OSError):
            return None

        parsed = ParsedFile(
            path=file_path,
            language=language,
            content=content,
            line_count=content.count("\n") + 1,
        )

        if language == "Python":
            self._parse_python(content, parsed)
        elif language in ("JavaScript", "TypeScript"):
            self._parse_js_ts(content, parsed)
        elif language == "Java":
            self._parse_java(content, parsed)
        elif language in ("C", "C++", "C/C++ Header", "C++ Header"):
            self._parse_c_cpp(content, parsed)
        elif language == "Go":
            self._parse_go(content, parsed)
        elif language == "Rust":
            self._parse_rust(content, parsed)
        elif language in ("C#",):
            self._parse_csharp(content, parsed)
        else:
            self._parse_generic(content, parsed)

        return parsed

    # ─── Python Parser ────────────────────────────────────────────────────
    def _parse_python(self, content: str, parsed: ParsedFile):
        lines = content.split("\n")

        # Extract imports
        import_pattern = re.compile(r"^(?:import|from)\s+(.+)", re.MULTILINE)
        for m in import_pattern.finditer(content):
            parsed.imports.append(m.group(0).strip())

        # Extract classes
        class_pattern = re.compile(
            r"^(class\s+(\w+)(?:\([^)]*\))?)\s*:", re.MULTILINE
        )
        for m in class_pattern.finditer(content):
            line_no = content[:m.start()].count("\n") + 1
            symbol = Symbol(
                name=m.group(2),
                kind="class",
                line_start=line_no,
                line_end=self._find_block_end(lines, line_no - 1),
                code=m.group(0),
            )
            parsed.symbols.append(symbol)

        # Extract functions and methods
        func_pattern = re.compile(
            r"^(\s*)(async\s+)?def\s+(\w+)\s*\(([^)]*)\)\s*(?:->[^:]+)?:",
            re.MULTILINE
        )
        for m in func_pattern.finditer(content):
            indent = len(m.group(1))
            line_no = content[:m.start()].count("\n") + 1
            func_name = m.group(3)
            end_line = self._find_block_end(lines, line_no - 1)
            func_code = "\n".join(lines[line_no - 1: min(end_line, line_no + 30)])

            kind = "method" if indent > 0 else "function"
            docstring = self._extract_python_docstring(lines, line_no)
            calls = self._extract_function_calls_python(func_code)

            symbol = Symbol(
                name=func_name,
                kind=kind,
                line_start=line_no,
                line_end=end_line,
                code=func_code,
                docstring=docstring,
                calls=calls,
            )
            parsed.symbols.append(symbol)

    def _find_block_end(self, lines: List[str], start_idx: int) -> int:
        """Find end of Python indented block."""
        if start_idx >= len(lines):
            return start_idx
        base_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
        for i in range(start_idx + 1, len(lines)):
            line = lines[i]
            if line.strip() == "":
                continue
            indent = len(line) - len(line.lstrip())
            if indent <= base_indent and line.strip():
                return i
        return len(lines)

    def _extract_python_docstring(self, lines: List[str], func_line: int) -> str:
        """Extract docstring from the line after a def."""
        idx = func_line  # 0-indexed line after def
        if idx >= len(lines):
            return ""
        next_line = lines[idx].strip()
        if next_line.startswith('"""') or next_line.startswith("'''"):
            quote = next_line[:3]
            if next_line.count(quote) >= 2 and len(next_line) > 6:
                return next_line.strip(quote).strip()
            docstring_lines = [next_line.lstrip(quote)]
            for i in range(idx + 1, min(idx + 20, len(lines))):
                docstring_lines.append(lines[i])
                if quote in lines[i]:
                    break
            return " ".join(docstring_lines).replace(quote, "").strip()
        return ""

    def _extract_function_calls_python(self, code: str) -> List[str]:
        """Extract function/method calls from Python code."""
        call_pattern = re.compile(r"(\w+)\s*\(")
        keywords = {
            "if", "while", "for", "return", "print", "len", "range",
            "str", "int", "float", "list", "dict", "tuple", "set",
            "isinstance", "type", "super", "property",
        }
        calls = []
        for m in call_pattern.finditer(code):
            name = m.group(1)
            if name not in keywords and not name[0].isupper():
                calls.append(name)
        return list(set(calls))

    # ─── JavaScript / TypeScript Parser ───────────────────────────────────
    def _parse_js_ts(self, content: str, parsed: ParsedFile):
        # Imports
        import_pat = re.compile(
            r"(?:import\s+.*?from\s+['\"].*?['\"]|require\s*\(['\"].*?['\"]\))",
            re.MULTILINE
        )
        for m in import_pat.finditer(content):
            parsed.imports.append(m.group(0).strip())

        # Classes
        class_pat = re.compile(r"class\s+(\w+)(?:\s+extends\s+\w+)?\s*\{", re.MULTILINE)
        for m in class_pat.finditer(content):
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=m.group(1), kind="class",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0),
            ))

        # Functions (various styles)
        patterns = [
            re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE),
            re.compile(r"(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(", re.MULTILINE),
            re.compile(r"(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?function", re.MULTILINE),
            re.compile(r"^\s+(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{", re.MULTILINE),
        ]
        seen = set()
        for pat in patterns:
            for m in pat.finditer(content):
                name = m.group(1)
                if name in seen:
                    continue
                seen.add(name)
                line_no = content[:m.start()].count("\n") + 1
                is_exported = "export" in m.group(0)
                parsed.symbols.append(Symbol(
                    name=name, kind="function",
                    line_start=line_no, line_end=line_no + 1,
                    code=m.group(0),
                    is_exported=is_exported,
                ))

    # ─── Java Parser ──────────────────────────────────────────────────────
    def _parse_java(self, content: str, parsed: ParsedFile):
        # Imports
        for m in re.finditer(r"^import\s+[\w.]+;", content, re.MULTILINE):
            parsed.imports.append(m.group(0))

        # Classes/interfaces
        for m in re.finditer(
            r"(?:public|private|protected|abstract)?\s*(?:class|interface|enum)\s+(\w+)",
            content, re.MULTILINE
        ):
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=m.group(1), kind="class",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0),
            ))

        # Methods
        method_pat = re.compile(
            r"(?:public|private|protected|static|final|synchronized|abstract|native|\s)+\s+"
            r"(?:[\w<>\[\]]+)\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{",
            re.MULTILINE
        )
        for m in method_pat.finditer(content):
            name = m.group(1)
            if name in ("if", "while", "for", "switch", "catch"):
                continue
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=name, kind="method",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0)[:200],
            ))

    # ─── C / C++ Parser ───────────────────────────────────────────────────
    def _parse_c_cpp(self, content: str, parsed: ParsedFile):
        # Includes
        for m in re.finditer(r"^#include\s+[<\"].*?[>\"]", content, re.MULTILINE):
            parsed.imports.append(m.group(0))

        # Classes/structs
        for m in re.finditer(r"(?:class|struct)\s+(\w+)\s*(?::\s*[\w\s,]+)?\s*\{", content, re.MULTILINE):
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=m.group(1), kind="class",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0),
            ))

        # Functions
        func_pat = re.compile(
            r"^[\w\s\*:&<>]+\s+(\w+)\s*\([^;{]*\)\s*(?:const)?\s*\{",
            re.MULTILINE
        )
        for m in func_pat.finditer(content):
            name = m.group(1)
            if name in ("if", "while", "for", "switch", "else", "main"):
                pass
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=name, kind="function",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0)[:200],
            ))

    # ─── Go Parser ────────────────────────────────────────────────────────
    def _parse_go(self, content: str, parsed: ParsedFile):
        for m in re.finditer(r"^import\s+", content, re.MULTILINE):
            parsed.imports.append(m.group(0))

        for m in re.finditer(r"^type\s+(\w+)\s+struct\s*\{", content, re.MULTILINE):
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=m.group(1), kind="class",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0),
            ))

        for m in re.finditer(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", content, re.MULTILINE):
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=m.group(1), kind="function",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0),
            ))

    # ─── Rust Parser ──────────────────────────────────────────────────────
    def _parse_rust(self, content: str, parsed: ParsedFile):
        for m in re.finditer(r"^use\s+[\w:]+", content, re.MULTILINE):
            parsed.imports.append(m.group(0))

        for m in re.finditer(r"(?:pub\s+)?struct\s+(\w+)", content, re.MULTILINE):
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=m.group(1), kind="class",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0),
            ))

        for m in re.finditer(r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]", content, re.MULTILINE):
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=m.group(1), kind="function",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0),
            ))

    # ─── C# Parser ────────────────────────────────────────────────────────
    def _parse_csharp(self, content: str, parsed: ParsedFile):
        for m in re.finditer(r"^using\s+[\w.]+;", content, re.MULTILINE):
            parsed.imports.append(m.group(0))

        for m in re.finditer(
            r"(?:public|private|protected|internal|abstract|sealed)?\s*class\s+(\w+)",
            content, re.MULTILINE
        ):
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=m.group(1), kind="class",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0),
            ))

        for m in re.finditer(
            r"(?:public|private|protected|internal|static|async|virtual|override|\s)+\s+"
            r"(?:[\w<>\[\]?]+)\s+(\w+)\s*\([^)]*\)\s*\{",
            content, re.MULTILINE
        ):
            name = m.group(1)
            if name in ("if", "while", "for", "switch"):
                continue
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=name, kind="method",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0)[:200],
            ))

    # ─── Generic Fallback ─────────────────────────────────────────────────
    def _parse_generic(self, content: str, parsed: ParsedFile):
        """Minimal fallback: extract anything that looks like a function."""
        for m in re.finditer(r"\bfunction\s+(\w+)\s*\(", content, re.MULTILINE):
            line_no = content[:m.start()].count("\n") + 1
            parsed.symbols.append(Symbol(
                name=m.group(1), kind="function",
                line_start=line_no, line_end=line_no + 1,
                code=m.group(0),
            ))
