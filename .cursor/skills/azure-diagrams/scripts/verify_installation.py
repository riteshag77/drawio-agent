#!/usr/bin/env python3
"""
Azure Architecture Diagrams - Installation Verification Script

Run this to verify all prerequisites are correctly installed. Output is plain
ASCII (no emoji) so it is safe on a Windows cp1252 console.

Usage:
    python verify_installation.py
"""

import importlib.metadata as _md
import shutil
import sys

# Minimum supported diagrams version. 0.25.1 fixed the missing-logo bug in
# 0.25.0 and ships the Azure AI/ML, containers, management-governance,
# databases(v2) and networking modules this skill relies on.
DIAGRAMS_FLOOR = (0, 25, 1)

OK = "[ OK ]"
FAIL = "[FAIL]"
WARN = "[WARN]"


def _version_tuple(v):
    parts = []
    for chunk in v.split("."):
        num = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def check_python_version():
    print("Checking Python version...", end=" ")
    if sys.version_info[:2] >= (3, 9):
        print(f"{OK} Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        return True
    print(f"{FAIL} Python {sys.version_info.major}.{sys.version_info.minor} (need 3.9+)")
    return False


def check_graphviz():
    print("Checking Graphviz (dot on PATH)...", end=" ")
    dot_path = shutil.which("dot")
    if dot_path:
        print(f"{OK} Found at {dot_path}")
        return True
    print(f"{FAIL} 'dot' not found on PATH")
    print("   Install Graphviz, then restart your terminal:")
    print("   - Windows: winget install -e --id Graphviz.Graphviz   (or: choco install graphviz)")
    print("   - macOS:   brew install graphviz")
    print("   - Linux:   sudo apt-get install graphviz")
    print("   If installed but still not found, add its 'bin' folder to PATH")
    print("   (Windows: C:\\Program Files\\Graphviz\\bin) and reopen the terminal.")
    return False


def check_diagrams_library():
    print("Checking diagrams library...", end=" ")
    try:
        version = _md.version("diagrams")
    except _md.PackageNotFoundError:
        print(f"{FAIL} Not found")
        print("   Install with: pip install -r requirements.txt   (or: pip install 'diagrams>=0.25.1')")
        return False
    floor = ".".join(map(str, DIAGRAMS_FLOOR))
    if _version_tuple(version) < DIAGRAMS_FLOOR:
        print(f"{FAIL} {version} is below the supported floor {floor}")
        print(f"   Upgrade with: pip install --upgrade 'diagrams>={floor}'")
        return False
    print(f"{OK} Version {version}")
    return True


def check_azure_providers():
    print("Checking Azure diagram providers...", end=" ")
    try:
        # Core providers
        from diagrams.azure.integration import LogicApps, ServiceBus, APIManagement  # noqa: F401
        from diagrams.azure.compute import FunctionApps, AKS, ACR  # noqa: F401
        from diagrams.azure.database import CosmosDb  # noqa: F401
        from diagrams.azure.storage import BlobStorage  # noqa: F401
        from diagrams.azure.security import KeyVaults  # noqa: F401
        # 0.25.x modules this skill depends on
        from diagrams.azure.networking import Bastions  # noqa: F401
        from diagrams.azure.aimachinelearning import CognitiveServices  # noqa: F401
        print(f"{OK} Core + 0.25.x providers available")
        return True
    except ImportError as e:
        print(f"{FAIL} {e}")
        print("   This usually means diagrams is below 0.25.1. Upgrade and re-run.")
        return False


def test_diagram_generation():
    print("Testing diagram generation (PNG)...", end=" ")
    try:
        import os
        import tempfile
        from diagrams import Diagram
        from diagrams.azure.integration import LogicApps

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "test")
            with Diagram("Test", show=False, filename=output_path, outformat="png"):
                LogicApps("Test")
            if os.path.exists(f"{output_path}.png"):
                print(f"{OK} Generated a test PNG")
                return True
            print(f"{FAIL} PNG file not created")
            return False
    except Exception as e:
        print(f"{FAIL} {e}")
        return False


def note_svg_renderer():
    """Informational only - never fails the run (PNG is the default output)."""
    print("Checking optional SVG renderer (cairosvg)...", end=" ")
    if importlib_util_find("cairosvg"):
        print(f"{OK} cairosvg available (portable SVG export enabled)")
    else:
        print(f"{WARN} cairosvg not installed - PNG works fine; install for portable SVG "
              "(pip install -r requirements-extras.txt)")


def importlib_util_find(name):
    import importlib.util
    return importlib.util.find_spec(name) is not None


def main():
    print("=" * 60)
    print("Azure Architecture Diagrams - Installation Verification")
    print("=" * 60)
    print()

    results = []
    results.append(("Python 3.9+", check_python_version()))
    results.append(("Graphviz", check_graphviz()))
    results.append(("Diagrams library >= 0.25.1", check_diagrams_library()))

    if results[-1][1]:
        results.append(("Azure providers", check_azure_providers()))
        results.append(("Diagram generation", test_diagram_generation()))

    note_svg_renderer()  # warning only, not part of pass/fail

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)

    all_passed = all(r[1] for r in results)
    for name, passed in results:
        print(f"  {name}: {OK if passed else FAIL}")
    print()

    if all_passed:
        print("All checks passed. You're ready to create Azure diagrams.")
        print()
        print("Try it out:")
        print('  "Create a simple Azure diagram with Logic Apps and Service Bus"')
    else:
        print("Some checks failed. Install the missing prerequisites above and re-run.")
        print()
        print("Quick start:")
        print("  winget install -e --id Graphviz.Graphviz   # Windows (macOS: brew install graphviz)")
        print("  pip install -r requirements.txt            # Python library (diagrams >= 0.25.1)")

    print()
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
