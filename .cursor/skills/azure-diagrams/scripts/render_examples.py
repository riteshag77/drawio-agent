#!/usr/bin/env python3
"""Execute the runnable diagram examples in the docs and confirm they render.

Selection rules (keeps CI low-noise):
  - only ```python fences that parse as a COMPLETE program (ast.parse succeeds) are
    run: either a `diagrams` program (imports Diagram and uses `with Diagram(`) or a
    `graphviz` program (imports Digraph, builds one, and calls `.render(`). Fragments
    and attribute snippets are skipped.
  - blocks that are illustrative-only are skipped: those containing `...`, an
    explicit "Do NOT"/"don't"/U+274C marker, or a sandbox path (/mnt/user-data).

Each selected block is exec'd in a throwaway working directory with output
suppressed. Exit code is non-zero if any block raises.

Usage:
    python render_examples.py [ROOT]
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
import sys
import tempfile

FENCE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL)
SKIP_MARKERS = ("...", "/mnt/user-data", "./icons", "do not", "don't", "❌")


def is_runnable(src: str) -> bool:
    low = src.lower()
    if any(m in low for m in SKIP_MARKERS):
        return False
    try:
        ast.parse(src)
    except SyntaxError:
        return False
    # diagrams program: must import Diagram itself and use it (skips illustrative
    # snippets that reference Diagram/nodes defined in a previous fence).
    if "with diagram(" in low and (
        re.search(r"^\s*from\s+diagrams\s+import\s+[^\n]*\bDiagram\b", src, re.M)
        or "import diagrams" in low
    ):
        return True
    # graphviz program: must import Digraph, build one, and render it.
    if "digraph(" in low and ".render(" in low and (
        "from graphviz import" in low or "import graphviz" in low
    ):
        return True
    return False


def main() -> int:
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent.parent
    blocks: list[tuple[pathlib.Path, int, str]] = []
    for path in sorted(root.rglob("*.md")):
        if ".venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, m in enumerate(FENCE.findall(text)):
            if is_runnable(m):
                blocks.append((path.relative_to(root), i, m))

    passed = failed = 0
    workdir = tempfile.mkdtemp()
    os.chdir(workdir)
    for path, idx, src in blocks:
        try:
            exec(compile(src, f"{path}#block{idx}", "exec"), {"__name__": "__main__"})
            passed += 1
        except Exception as e:  # noqa: BLE001 - we want to report any failure
            failed += 1
            print(f"  FAIL {path} (block {idx}): {type(e).__name__}: {e}")

    print(f"\nrendered {passed} example block(s); {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
