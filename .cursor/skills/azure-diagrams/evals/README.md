# Evals

Two layers of evaluation for this skill.

## 1. Automated correctness (runs in CI)

These guarantee that everything the skill ships actually works, and run on every push/PR
(`.github/workflows/ci.yml`):

| Check | Script | Guarantees |
|-------|--------|------------|
| Imports | `scripts/check_imports.py` | every `diagrams` import in the docs resolves against the pinned library |
| Rendering | `scripts/render_examples.py` | every runnable example block (diagrams + graphviz) renders without error |
| IaC parser | `scripts/iac_to_diagram.py tests/fixtures/sample-arm.json --render` | the ARM parser produces a rendering diagram |
| Prereqs | `scripts/verify_installation.py` | Graphviz + diagrams floor are present |

Run them all locally:

```bash
python scripts/check_imports.py
python scripts/render_examples.py
python scripts/iac_to_diagram.py tests/fixtures/sample-arm.json --render
```

## 2. Behavioural scenarios (manual or LLM-judge)

`scenarios.md` lists triggering and task scenarios with expected behaviours and pass
criteria. Run them by giving the prompt to an agent with this skill installed (Claude Code,
Copilot, Cursor, ...) and checking the result against the pass criteria. They are designed to
be model- and tool-agnostic, and are the gate for any change to the trigger description.
