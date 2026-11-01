"""What the stored corpus can and cannot draw, and *why* — ranked by cause.

The question this answers is "which page does a user not get, and what is the
one reason it happens most often". Nothing else answered it. Four separate
defects were found by hand in one sitting, all of them months old, all of them
invisible to the existing checks:

* Unit tests asked "does this repair work?" — every one of them passed, because
  each mechanism was correct in isolation. What was broken was the *gate* in
  front of it, so the repair simply never ran.
* ``replay_captures.py`` asks "did drawing change?" and compares geometry
  digests. A page that never compiles has no geometry to move, so it is
  invisible there by construction (it now counts them, which is how you notice
  the number; this is how you find out what to do about it).
* Nothing at all ranked failures by cause, so a defect costing 122 views looked
  the same as one costing 2 — a list of unrelated-looking error strings.

Two outputs, and the second is the point:

1. **Where every view ends up**: drawn as-is, rescued by a repair, or lost.
2. **A ranked cause table for the lost ones** — a work queue in priority order,
   rather than a pile of strings someone has to group in their head.

It also reports **how many views each repair rescued**, which is the standing
detector for the bug class above. A backstop whose rescue count falls to zero
has had its gate broken, and that is a regression even though every unit test
still passes and no geometry moved.

Judged by replaying stored IRs through the same repair sequence the pipeline
runs (``sanitize_raw`` at parse, then ``_drop_invalid_flows``, then the flow
spine). It does not include the corrective LLM pass, which needs a provider —
so "lost" means "lost unless the model fixes it on the re-ask", an upper bound
on real user impact rather than a live defect count.

Usage
-----
    python tools/corpus_health.py                # summary + ranked causes
    python tools/corpus_health.py --verbose      # name the captures per cause
    python tools/corpus_health.py --json out.json
    python tools/corpus_health.py --max-lost 40  # non-zero exit above a budget

Local-only, like replay_captures.py: ``data/`` is gitignored, so on a runner
this finds nothing to report.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore")

from app.core.services.agent import (  # noqa: E402
    _drop_invalid_flows,
    _drop_orphan_cells,
    _is_orphan_only,
)
from app.domain.ir import IR_TYPES_BY_VIEW  # noqa: E402
from app.domain.ir.flow_spine import synthesize_spine_flows  # noqa: E402
from tools.e2e_capture_compare import extract_irs_from_capture  # noqa: E402

#: Error text -> short, stable cause key. Ordered: the first match wins, so
#: put the specific before the general. Keys are what the ranking groups on,
#: so they must stay stable across wording changes to the messages themselves.
_CAUSES: tuple[tuple[str, str], ...] = (
    ("not a known cell id", "dangling flow endpoint"),
    ("flows is empty", "no flows emitted"),
    ("zero flows", "no flows emitted"),
    ("no flow connecting", "unconnected leaf or actor"),
    ("replica/standby", "replica modelled on Logical"),
    ("subnet tier", "network spans one tier only"),
    ("straight out to an external actor", "data tier exposed to an actor"),
    ("is not meaningful", "self-loop flow"),
    ("cross-cutting", "edge touches a cross-cutting group"),
)


def classify(error: str) -> str:
    for needle, cause in _CAUSES:
        if needle in error:
            return cause
    # Unrecognised text is reported verbatim (trimmed) rather than bucketed as
    # "other": a new failure mode should be conspicuous, not absorbed.
    return error.split(" — ")[0][:60]


def _preparse_effect(ir_type: type, raw: dict) -> tuple[int, int]:
    """``(leaves_removed, flows_rewritten)`` by ``sanitize_raw``.

    Pre-parse repairs (the infra/replica strips and the bridging that follows
    them) run inside ``model_validate``, so their contribution is otherwise
    invisible — the IR that comes out is simply valid, with nothing to say it
    was repaired on the way. Measured by sanitizing a copy and diffing.
    """
    sanitize = getattr(ir_type, "sanitize_raw", None)
    if not callable(sanitize):
        return 0, 0

    def _leaf_ids(d: dict) -> set[str]:
        return {
            leaf.get("id")
            for key in ("corridors", "anchored")
            for c in (d.get(key) or [])
            if isinstance(c, dict)
            for leaf in (c.get("leaves") or [])
            if isinstance(leaf, dict)
        }

    def _flows(d: dict) -> list[tuple]:
        return sorted(
            (f.get("source"), f.get("target"))
            for f in (d.get("flows") or []) if isinstance(f, dict)
        )

    try:
        cleaned = sanitize(deepcopy(raw))
    except Exception:
        return 0, 0
    if not isinstance(cleaned, dict):
        return 0, 0
    removed = len(_leaf_ids(raw) - _leaf_ids(cleaned))
    rewritten = 0 if _flows(raw) == _flows(cleaned) else 1
    return removed, rewritten


def assess_view(view: str, raw: dict) -> dict:
    """Replay one view through the pipeline's repair sequence.

    Returns its outcome, which repair (if any) saved it, and the causes still
    outstanding.
    """
    ir_type = IR_TYPES_BY_VIEW[view]
    removed, rewritten = _preparse_effect(ir_type, raw)

    try:
        ir = ir_type.model_validate(deepcopy(raw))
    except Exception as exc:
        return {"view": view, "outcome": "unparseable", "repair": "",
                "causes": [f"does not parse: {type(exc).__name__}"]}

    errors = ir.semantic_errors()
    if not errors:
        repair = "pre-parse strip/bridge" if (removed or rewritten) else ""
        return {"view": view, "outcome": "rescued" if repair else "clean",
                "repair": repair, "causes": []}

    # Same order as agent._generate_ir_with_corrections' residual handling.
    _drop_invalid_flows(ir, errors, None, view)
    if not ir.semantic_errors():
        return {"view": view, "outcome": "rescued", "repair": "drop dangling flows", "causes": []}

    if not getattr(ir, "flows", None) and synthesize_spine_flows(ir):
        if not ir.semantic_errors():
            return {"view": view, "outcome": "rescued", "repair": "synthesized flow spine",
                    "causes": []}

    residual = ir.semantic_errors()
    if _is_orphan_only(residual):
        _drop_orphan_cells(ir, None, view)
        if not ir.semantic_errors():
            return {"view": view, "outcome": "rescued", "repair": "dropped unconnected cells",
                    "causes": []}

    return {"view": view, "outcome": "lost", "repair": "",
            "causes": sorted({classify(e) for e in ir.semantic_errors()})}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true", help="name the captures behind each cause")
    ap.add_argument("--json", metavar="PATH", help="write the full per-view table as JSON")
    ap.add_argument("--max-lost", type=int, metavar="N",
                    help="exit non-zero when more than N views are lost")
    args = ap.parse_args()

    caps = sorted((ROOT / "data" / "captures").glob("*.json"))
    if not caps:
        print("No captures in data/captures — nothing to report.")
        return 0

    rows: list[dict] = []
    for path in caps:
        try:
            irs = extract_irs_from_capture(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        for view, raw in (irs or {}).items():
            if view not in IR_TYPES_BY_VIEW:
                continue
            row = assess_view(view, raw)
            row["capture"] = path.stem[-8:]
            rows.append(row)

    if not rows:
        print("No cloud-view IRs in the corpus — nothing to report.")
        return 0

    outcomes = Counter(r["outcome"] for r in rows)
    repairs = Counter(r["repair"] for r in rows if r["repair"])
    lost = [r for r in rows if r["outcome"] == "lost"]

    print(f"Replayed {len(rows)} view(s) from {len(caps)} capture(s) "
          f"through the pipeline's repair sequence\n")
    for label, key in (("drawn as-is", "clean"), ("rescued by a repair", "rescued"),
                       ("LOST", "lost"), ("unparseable", "unparseable")):
        if outcomes.get(key):
            print(f"  {outcomes[key]:4d}  {label}")

    if repairs:
        # A backstop that stops rescuing has had its gate broken — which is a
        # regression no unit test and no geometry digest will report, because
        # the mechanism itself is still correct and nothing moved on a page.
        print("\n  what rescued them:")
        for repair, n in repairs.most_common():
            print(f"    {n:4d}  {repair}")

    if lost:
        by_cause: Counter[str] = Counter()
        captures_for: dict[str, set[str]] = defaultdict(set)
        for row in lost:
            for cause in row["causes"]:
                by_cause[cause] += 1
                captures_for[cause].add(f"{row['capture']}[{row['view']}]")
        print("\n  why the lost ones were lost (a view can have more than one):")
        for cause, n in by_cause.most_common():
            print(f"    {n:4d}  {cause}")
            if args.verbose:
                for ref in sorted(captures_for[cause]):
                    print(f"           {ref}")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")

    if args.max_lost is not None and len(lost) > args.max_lost:
        print(f"\nFAIL — {len(lost)} lost view(s), budget is {args.max_lost}.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
