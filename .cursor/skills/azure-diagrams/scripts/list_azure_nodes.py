#!/usr/bin/env python3
"""List every Azure node class shipped by the installed `diagrams` library.

This is the single source of truth for the Azure component catalogue. Class names
must never be hand-typed into the reference docs (that is how misspelled,
non-existent names crept in); generate them from the pinned library instead.

Usage:
    python list_azure_nodes.py            # human-readable summary to stdout
    python list_azure_nodes.py --json     # machine-readable {module: [classes]}
    python list_azure_nodes.py --markdown # markdown catalogue (module headings + class lists)

Exit code is non-zero if the installed diagrams version is below the supported floor.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import inspect
import json
import pkgutil
import sys

FLOOR = (0, 25, 1)


def _version_tuple(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in v.split("."):
        num = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def collect() -> dict[str, list[str]]:
    """Return {submodule_name: [NodeClass, ...]} for diagrams.azure.*"""
    from diagrams import Node
    import diagrams.azure as az

    inventory: dict[str, list[str]] = {}
    for mod in pkgutil.iter_modules(az.__path__, az.__name__ + "."):
        m = importlib.import_module(mod.name)
        classes = sorted(
            name
            for name, obj in inspect.getmembers(m, inspect.isclass)
            if issubclass(obj, Node) and obj.__module__ == m.__name__
        )
        if classes:
            inventory[mod.name.split(".")[-1]] = classes
    return inventory


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--markdown", action="store_true", help="emit markdown catalogue")
    args = ap.parse_args()

    version = md.version("diagrams")
    inventory = collect()
    total = sum(len(v) for v in inventory.values())

    if args.json:
        print(json.dumps({"version": version, "total": total, "modules": inventory}, indent=2))
    elif args.markdown:
        print(f"<!-- Generated from diagrams {version}: {total} Azure node classes across "
              f"{len(inventory)} modules. Regenerate with scripts/list_azure_nodes.py --markdown -->\n")
        for module in sorted(inventory):
            print(f"### `diagrams.azure.{module}` ({len(inventory[module])})\n")
            print(", ".join(f"`{c}`" for c in inventory[module]))
            print()
    else:
        print(f"diagrams {version}: {total} Azure node classes across {len(inventory)} modules\n")
        for module in sorted(inventory):
            print(f"  {module:<22} {len(inventory[module]):>4}")

    if _version_tuple(version) < FLOOR:
        floor = ".".join(map(str, FLOOR))
        print(f"\nERROR: diagrams {version} is below the supported floor {floor}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
