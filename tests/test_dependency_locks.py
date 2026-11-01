"""Every declared dependency must actually be in the lock CI installs from.

`playwright` was declared in requirements-dev.txt and absent from
requirements-dev.lock for as long as both files existed. CI installs from the
lock, so the one frontend test in the repo could not run there — and because
that test *skips* when the browser is missing, the job stayed green while
testing nothing. Two mechanisms, each individually reasonable, adding up to a
declared dependency that was never installed anywhere.

Nothing else checks this. `pip install -r` on the lock succeeds whether or not
the lock reflects the .txt, and a missing entry only shows up as a skipped or
absent test somewhere downstream.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: `name==version`, `name>=x,<y`, `name ; marker`, `-r other.txt`, comments.
_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[<>=!~;\[].*)?$")


def _declared(path: Path) -> set[str]:
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _REQUIREMENT.match(line)
        if match:
            names.add(match.group(1).lower().replace("_", "-"))
    return names


class DependencyLockTests(unittest.TestCase):
    def _assert_covered(self, source: str, lock: str) -> None:
        declared = _declared(ROOT / source)
        locked = _declared(ROOT / lock)
        self.assertTrue(declared, f"{source} parsed to nothing — the parser is wrong")
        missing = sorted(declared - locked)
        self.assertEqual(
            missing, [],
            f"{source} declares {missing} but {lock} does not contain them. CI installs "
            f"from the lock, so these are not present anywhere it runs. Regenerate with "
            f"the `uv pip compile` command in the lock's header.",
        )

    def test_runtime_requirements_are_locked(self) -> None:
        self._assert_covered("requirements.txt", "requirements.lock")

    def test_dev_requirements_are_locked(self) -> None:
        self._assert_covered("requirements-dev.txt", "requirements-dev.lock")

    def test_the_browser_test_dependency_is_installed_by_ci(self) -> None:
        """The specific hole this file was written for."""
        self.assertIn("playwright", _declared(ROOT / "requirements-dev.lock"))


if __name__ == "__main__":
    unittest.main()
