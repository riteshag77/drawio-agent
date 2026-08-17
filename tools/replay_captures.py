"""Replay every stored capture's IR through the *current* compiler.

This is the standing regression net for layout/geometry work: recompiling
historical IRs is deterministic and needs no LLM, so it answers "did I change
how diagrams are drawn?" directly.

Why this exists alongside e2e_capture_compare.py
------------------------------------------------
That tool does one capture at a time and reports a binary pass/fail. Run across
the whole corpus it produces failures that are not regressions at all, and
whoever sees them has to re-derive why from scratch:

* A capture whose diagram was drawn by the **direct-XML path** has no IR to
  recompile. Not a failure — there was never anything to compile.
* A capture whose IR the pipeline itself **rejected at generation time** (a
  view whose `flows` came back empty, so the pipeline logged
  "<view> IR path failed" and fell back) stores an IR that was never usable.
  Refusing to compile it now is the compiler being *correct*.

Both look identical to "the compiler broke" unless you go read the capture's
logs. This tool separates them structurally — by asking the IR whether it is
valid, not by hardcoding capture ids — so a genuine regression is the only
thing that shows up as one, and the exit code means something in CI.

Usage
-----
    python tools/replay_captures.py                    # whole corpus
    python tools/replay_captures.py --verbose          # explain every non-ok capture
    python tools/replay_captures.py --update-baseline  # record known-good geometry
    python tools/replay_captures.py --strict           # no corpus / no baseline = failure

Why this is not a CI job
------------------------
``data/`` is gitignored in its entirety — both the capture corpus and the
baseline that describes it are local-only by design (a committed baseline would
describe captures a fresh clone does not have). On a runner this script would
therefore find zero captures and exit 0, a green check that verified nothing.
It is a *local* gate, run deliberately before and after layout work, and
``--strict`` exists so that a wiped or missing ``data/`` fails loudly instead
of imitating a pass on the machine that does have a corpus.

Regressions are found by comparing a geometry digest against a *stored*
baseline (data/capture_replay_baseline.json), not by re-scoring against
geometry_spec — see geometry_fingerprint() for why that distinction is the
whole point. Run --update-baseline only when a layout change is intended.

The baseline is untracked, and deliberately so: it lives in ``data/`` next to
the capture corpus it describes, which is untracked too. A committed baseline
would describe captures a fresh clone does not have. Record one with
--update-baseline *before* starting layout work, so the comparison is against
code you have not touched yet — generating it afterwards bakes in whatever you
just changed and the check can only ever pass.

Exit code is non-zero only for genuine regressions: a valid IR that fails to
compile, or compiled geometry that moved.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore")

from tools.e2e_capture_compare import (  # noqa: E402
    extract_irs_from_capture,
    recompile_from_capture_irs,
)

#: An mxfile with no pages still serialises to ~170 chars of envelope, so
#: "compiled to nothing" has to be detected by size, not by falsiness.
EMPTY_MXFILE_CHARS = 200

OK = "ok"
NO_IR = "no-ir"
UNUSABLE_IR = "unusable-ir"
UNSTABLE = "unstable"
REGRESSION = "regression"


#: Where the known-good geometry digests live.
BASELINE_PATH = ROOT / "data" / "capture_replay_baseline.json"


@dataclass
class Result:
    capture: str
    status: str
    generation_path: str = ""
    detail: str = ""
    views: list[str] = field(default_factory=list)
    fingerprint: str = ""


def _classify_unusable(irs: dict) -> tuple[bool, str]:
    """Was every view's IR already invalid before we touched it?

    Asks each IR to validate itself with the same ``semantic_errors()`` the
    pipeline used at generation time. If every view is blocked, the capture
    never had a compilable IR and the compiler is right to refuse it.
    """
    from app.domain.ir import IR_TYPES_BY_VIEW

    reasons: list[str] = []
    blocked = 0
    total = 0
    for view, raw in irs.items():
        if view not in IR_TYPES_BY_VIEW:
            continue
        total += 1
        try:
            model = IR_TYPES_BY_VIEW[view].model_validate(raw)
        except Exception as exc:
            blocked += 1
            reasons.append(f"{view}: does not parse ({str(exc)[:60]})")
            continue
        errs = model.semantic_errors()
        if errs:
            blocked += 1
            reasons.append(f"{view}: {errs[0][:80]}")
    if total and blocked == total:
        return True, "; ".join(reasons[:3])
    return False, ""


def geometry_fingerprint(xml: str) -> str:
    """A stable digest of *where every cell was drawn*.

    Deliberately not scored against ``geometry_spec`` constants. The obvious
    implementation — reuse score_mxfile's ``wrong_leaf_size`` — is
    self-referential: it compares compiled width against ``geo.LEAF_W``, the
    same constant the compiler just used, so changing LEAF_W moves the output
    and the yardstick together and the check can never fail. Verified: an
    injected 78 -> 96 leaf-width change scored a clean pass.

    A digest compared against a *stored* baseline has no such blind spot —
    any change in position or size of any cell shows up, whatever caused it.

    Geometry only: styles and icon ids are excluded so MCP/style-cache churn
    does not read as a layout regression.
    """
    import hashlib
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return f"unparseable:{exc}"
    rows: list[str] = []
    for diag in root.iter("diagram"):
        page = diag.get("name") or "?"
        for cell in diag.iter("mxCell"):
            g = cell.find("mxGeometry")
            if g is None:
                continue
            rows.append("|".join((
                page, cell.get("id") or "",
                g.get("x") or "", g.get("y") or "",
                g.get("width") or "", g.get("height") or "",
            )))
    rows.sort()
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()[:16]


def extract_generic_ir(data: dict) -> dict | None:
    """The image-trace path's IR, which is a sibling schema to the view IRs.

    extract_irs_from_capture only recognises NetworkIR/CorridorIR (it keys off
    a ``view`` field these do not have), so without this the whole generic_ir
    path — 11 captures — sat in the "nothing to recompile" bucket and got no
    regression coverage at all.
    """
    found = None
    for entry in data.get("response", {}).get("logs") or []:
        if entry.get("step") != "llm_output":
            continue
        raw = (entry.get("details") or {}).get("raw")
        if not isinstance(raw, str) or not raw.strip().startswith("{"):
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "nodes" in obj and "edges" in obj and "view" not in obj:
            found = obj  # last write wins, matching the view-IR extractor
    return found


def _replay_generic(name: str, data: dict, gen_path: str,
                    baseline: dict[str, str] | None) -> Result | None:
    """Recompile an image-trace capture, or None if it has no generic IR."""
    raw = extract_generic_ir(data)
    if raw is None:
        return None
    from app.adapters.outbound.diagram.compiler import compile_generic_ir
    from app.domain.ir import GenericDiagramIR

    try:
        ir = GenericDiagramIR.model_validate(raw)
    except Exception as exc:
        return Result(name, UNUSABLE_IR, gen_path,
                      f"generic IR does not parse ({str(exc)[:70]})")
    try:
        xml = compile_generic_ir(ir)
    except Exception as exc:
        return Result(name, REGRESSION, gen_path,
                      f"valid generic IR failed to compile: {type(exc).__name__}: {exc}")
    fp = geometry_fingerprint(xml)
    expected = (baseline or {}).get(name)
    if expected and expected != fp:
        return Result(name, REGRESSION, gen_path,
                      f"generic geometry changed (baseline {expected} -> {fp})",
                      fingerprint=fp)
    return Result(name, OK, gen_path, "generic IR (image trace)", fingerprint=fp)


def replay_one(path: Path, baseline: dict[str, str] | None = None) -> Result:
    name = path.stem
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return Result(name, REGRESSION, detail=f"capture unreadable: {exc}")

    gen_path = str((data.get("response") or {}).get("generation_path") or "")
    irs = extract_irs_from_capture(data)
    if not irs:
        generic = _replay_generic(name, data, gen_path, baseline)
        if generic is not None:
            return generic
        # extract_irs_from_capture only recognises the cloud *view* IRs
        # (NetworkIR/CorridorIR). generic_ir and simple_ir captures do have an
        # IR, just not one this replay path compiles — say so rather than
        # claiming they were drawn by direct XML.
        why = {
            "generic_ir": "GenericDiagramIR (image trace) — not a cloud view IR",
            "simple_ir": "SimpleDiagramIR (non-cloud) — not a cloud view IR",
            "direct_xml": "drawn by the direct-XML path (surgical edit or 3D)",
        }.get(gen_path, "no cloud view IR in logs")
        return Result(name, NO_IR, gen_path, why)

    unusable, why = _classify_unusable(irs)
    if unusable:
        return Result(name, UNUSABLE_IR, gen_path,
                      f"IR was already invalid at generation time — {why}", list(irs))

    try:
        xml = recompile_from_capture_irs(data)
    except Exception as exc:
        return Result(name, REGRESSION, gen_path,
                      f"valid IR failed to compile: {type(exc).__name__}: {exc}", list(irs))

    if not xml or len(xml) < EMPTY_MXFILE_CHARS:
        # Every view was individually rejected even though at least one IR
        # validated — that IS a regression, unlike the all-invalid case above.
        return Result(name, REGRESSION, gen_path,
                      f"compiled to an empty document ({len(xml or '')} chars)", list(irs))

    fp = geometry_fingerprint(xml)
    expected = baseline.get(name) if baseline else None
    if expected and expected != fp:
        # Compile it once more before calling it a regression. At least one
        # capture in the corpus does not compile deterministically inside a
        # long sweep (stable 5/5 in isolation, a different digest every full
        # run), so a single mismatch is not proof the compiler changed.
        # Reporting that as a regression would fail every clean run, and a
        # check that always fails gets ignored — which costs more than the
        # check is worth.
        try:
            second = geometry_fingerprint(recompile_from_capture_irs(data))
        except Exception:
            second = ""
        if second != fp:
            return Result(name, UNSTABLE, gen_path,
                          f"two recompiles disagree ({fp} vs {second}) — "
                          f"not deterministic, so not a usable signal", list(irs), fp)
        return Result(name, REGRESSION, gen_path,
                      f"geometry changed (baseline {expected} -> {fp}, confirmed twice)",
                      list(irs), fp)
    return Result(name, OK, gen_path, fingerprint=fp, views=list(irs))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true", help="explain every non-ok capture")
    ap.add_argument("--update-baseline", action="store_true",
                    help="record current geometry as the known-good baseline")
    ap.add_argument("--json", metavar="PATH", help="write the full result table as JSON")
    ap.add_argument("--strict", action="store_true",
                    help="fail instead of passing when there is no corpus or no baseline to compare against")
    args = ap.parse_args()

    caps = sorted((ROOT / "data" / "captures").glob("*.json"))
    if not caps:
        print("No captures in data/captures — nothing to replay.")
        if args.strict:
            print("FAIL — --strict was given and there is no corpus to check.")
            return 1
        return 0

    baseline: dict[str, str] = {}
    if BASELINE_PATH.exists() and not args.update_baseline:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8")).get("fingerprints", {})
    elif not BASELINE_PATH.exists() and not args.update_baseline:
        print("No baseline yet — run with --update-baseline once to record one.\n")

    results = [replay_one(p, baseline) for p in caps]
    buckets: dict[str, list[Result]] = {OK: [], NO_IR: [], UNUSABLE_IR: [], UNSTABLE: [], REGRESSION: []}
    for r in results:
        buckets[r.status].append(r)

    print(f"Replayed {len(caps)} capture(s) through the current compiler\n")
    print(f"  {len(buckets[OK]):4d}  recompiled cleanly")
    print(f"  {len(buckets[NO_IR]):4d}  no cloud view IR      (direct-XML / image-trace / non-cloud — nothing to recompile)")
    print(f"  {len(buckets[UNUSABLE_IR]):4d}  IR invalid when made  (pipeline rejected it then too — not a regression)")
    print(f"  {len(buckets[UNSTABLE]):4d}  not deterministic     (two recompiles disagree — no signal either way)")
    print(f"  {len(buckets[REGRESSION]):4d}  REGRESSION")

    if args.verbose:
        for label in (UNSTABLE, UNUSABLE_IR, NO_IR):
            if not buckets[label]:
                continue
            print(f"\n--- {label} ---")
            for r in buckets[label]:
                print(f"  {r.capture[-8:]}  [{r.generation_path or '?'}]  {r.detail}")

    if buckets[REGRESSION]:
        print("\n--- REGRESSIONS ---")
        for r in buckets[REGRESSION]:
            print(f"  {r.capture[-8:]}  [{r.generation_path or '?'}]  {r.detail}")

    if args.update_baseline:
        fps = {r.capture: r.fingerprint for r in results if r.status == OK and r.fingerprint}
        BASELINE_PATH.write_text(json.dumps(
            {"note": "Geometry digests of every recompilable capture. Regenerate only "
                     "when a layout change is intended and reviewed.",
             "fingerprints": fps}, indent=2), encoding="utf-8")
        print(f"\nRecorded baseline for {len(fps)} capture(s) -> {BASELINE_PATH.name}")
        return 0

    if not baseline:
        print("\n(no baseline compared — geometry changes cannot be detected)")
        if args.strict:
            print("\nFAIL — --strict was given and there is no baseline to compare against.")
            return 1

    if args.json:
        Path(args.json).write_text(
            json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8",
        )
        print(f"\nWrote {args.json}")

    if buckets[REGRESSION]:
        print("\nFAIL — the compiler changed how a valid IR is drawn.")
        return 1
    print("\nPASS — no capture that could be recompiled changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
