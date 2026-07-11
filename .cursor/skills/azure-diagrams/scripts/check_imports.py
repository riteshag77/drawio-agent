#!/usr/bin/env python3
"""Validate every `diagrams` import used in the skill against the installed library.

Scans Markdown ```python fences and the .py scripts, extracts import statements,
and checks each imported symbol actually resolves in the installed diagrams
version. Reports every unresolved import as file:line so docs can never ship a
class name that does not exist (the root cause of the Bastions/Speech bugs).

Usage:
    python check_imports.py [ROOT]      # ROOT defaults to the skill directory
Exit code is non-zero if any unresolved import is found.
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import re
import sys

FENCE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL)


def _check_source(src: str) -> list[tuple[str, str]]:
    """Return [(module, name)] that fail to resolve. Best-effort on partial snippets."""
    problems: list[tuple[str, str]] = []
    try:
        tree = ast.parse(src)
        nodes = list(ast.walk(tree))
    except SyntaxError:
        # Partial snippet: line-scan only `from diagrams ... import ...` (single line).
        nodes = []
        for line in src.splitlines():
            m = re.match(r"\s*from\s+(diagrams[\w.]*)\s+import\s+(.+)", line)
            if m and "(" not in m.group(2):
                mod = m.group(1)
                for name in (n.strip().split(" as ")[0] for n in m.group(2).split(",")):
                    if name and name != "*":
                        problems += _resolve(mod, name)
        return problems

    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("diagrams"):
            for alias in node.names:
                if alias.name != "*":
                    problems += _resolve(node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("diagrams"):
                    problems += _resolve(alias.name, None)
    return problems


def _resolve(module: str, name: str | None) -> list[tuple[str, str]]:
    try:
        m = importlib.import_module(module)
    except Exception:
        return [(module, name or "<module>")]
    if name and not hasattr(m, name):
        return [(module, name)]
    return []


def main() -> int:
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent.parent
    failures = 0
    files = sorted(root.rglob("*.md")) + sorted(root.rglob("*.py"))
    for path in files:
        if ".venv" in path.parts or "__pycache__" in path.parts or path.name == "check_imports.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = FENCE.findall(text) if path.suffix == ".md" else [text]
        seen: set[tuple[str, str]] = set()
        for block in blocks:
            for module, name in _check_source(block):
                if (module, name) not in seen:
                    seen.add((module, name))
        if seen:
            rel = path.relative_to(root)
            for module, name in sorted(seen):
                print(f"  {rel}: cannot import {name} from {module}")
                failures += 1
    print(f"\n{failures} unresolved diagrams import(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
