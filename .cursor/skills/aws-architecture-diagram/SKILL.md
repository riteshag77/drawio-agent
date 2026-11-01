---
name: aws-architecture-diagram
description: "Generate AWS architecture diagrams as draw.io XML with official AWS4 icons. Use for AWS-only architecture / system / draw.io diagrams (not Azure Graphviz PNG or multi-cloud general draw.io)."
argument-hint: "[describe your architecture or say 'analyze' to scan codebase]"
allowed-tools: Bash, Write, Read, Glob, Grep
user-invocable: true
---

## Routing (Cursor chat only)

- **Use this skill** for AWS-only hand-authored `.drawio` XML (AWS4 icons).
- **azure-diagrams** for Azure/`diagrams`+Graphviz PNG.
- **drawio-skill** for general, multi-cloud, or GCP draw.io XML.
- Do **not** paste these XML rules into the FastAPI app custom system message (runtime emits ArchitectureDiagram JSON).

You generate clean **left-to-right** AWS architecture diagrams as `.drawio` XML: **78×78** service icons, verified `mxgraph.aws4` stencils, orthogonal edges, no AWS Reference Architecture chrome (no category tint boxes, no teal step badges, no right-sidebar legend, no `light-dark()` fills).

## Workflow

### Step 0 — Audience

If unclear, ask once: **Technical** (service names, HTTPS/gRPC) or **Executive** (short action labels, numbered flow on edges). Default: Technical.

### Step 1 — Mode

**Mode A — Codebase analysis** (`analyze` / CFN / CDK / `aws_*` Terraform): extract services and flows; confirm with user; then generate.

**Mode B — Brainstorm**: ask 3–5 focused questions if needed, propose, then generate.

### Step 2 — Generate XML

Load on demand (not before this step):

1. `references/layout-guidelines.md` — LTR spacing and edge routing
2. `references/style-guide.md` — icon patterns, groups, canvas
3. Category icon files as needed: `references/aws4-shapes-services.md` (never guess stencil names)
4. Optional: `references/xml-templates-structure.md` for wrappers only

Do **not** open example `.drawio` files unless the user asks to match an existing diagram.

### Step 3 — Validate and export

1. Write `./docs/<descriptive-kebab-name>.drawio`
2. Run validators / PostToolUse hooks if present
3. Export PNG/SVG/PDF via draw.io CLI when requested (`references/cli-export.md`)
4. Write a short companion `./docs/<same-name>.md` (title, numbered flow, service list + purpose)
5. Two-step QA when exporting PNG: review PNG for empty icons / overlaps; fix XML and re-export

## Layout (mandatory)

- **Left-to-right** primary request path
- Users / UI on the **LEFT**; data stores / external systems on the **RIGHT**
- Parallel paths as horizontal lanes (top / bottom)
- **Min 220px** horizontal gap between icons; **min 250px** vertical between lanes
- Auxiliary / monitoring / DLQ **below** main flow with **≥280px** gap
- Canvas: `pageWidth="2400"` `pageHeight="1400"` (or larger); `dx="2800"` `dy="1600"`
- Nest real boundaries only (AWS Cloud → Region → VPC → subnet). Prefer flat service rows inside.

## Icons

- Size: **78×78** main services; **65×65** secondary
- `sketch=0;outlineConnect=0;`
- Font: Helvetica, **12px** labels under icons
- **Two patterns (never confuse):**
  - Service-level: `shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.<name>;strokeColor=#ffffff;fillColor=<category>`
  - Resource-level (ALB, client, etc.): `shape=mxgraph.aws4.<name>;strokeColor=none;fillColor=<category>`
- Look up every stencil in the references. Gotchas: OpenSearch → `elasticsearch_service`; EventBridge → `eventbridge`; never invent names.

## Edges

Base style:
`edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;strokeWidth=2;exitX=1;exitY=0.5;entryX=0;entryY=0.5;`

- Labels: 1–2 words max; `labelBackgroundColor=#F5F5F5;fontSize=11`
- **Omit `value` entirely** when unlabeled (no `value=""`)
- Do **not** label obvious hops (e.g. Lambda → DynamoDB “Write”)
- Vertical to aux band: exit bottom / enter top (`exitY=1` / `entryY=0`)
- Solid = primary path; dashed = async/protects; red dashed = error/DLQ
- Cross-container edges: `parent="1"`

## Groups

- Groups use `fillColor=none` (no tinted category boxes, no 120×120 icon frames)
- AWS Cloud / VPC / subnet: `shape=mxgraph.aws4.group;grIcon=...;spacingLeft=40;spacingTop=-4;container=1;dropTarget=1;`
- Children use `parent="<group-id>"` with geometry relative to the group

## Canvas polish

1. Full-page `#F5F5F5` background rect as first content cell (PNG export fix)
2. Title block (architecture name + optional author/date) below that
3. No right-sidebar legend; no on-diagram teal step badges — put narrative in the companion `.md` (and optional edge circled numbers for executive mode)

## Important Rules

- Uncompressed `<mxfile><diagram><mxGraphModel>` with cells `0` and `1`
- No XML comments; escape `&amp;` `&lt;` `&gt;` `&quot;`
- Unique cell ids; every edge has `<mxGeometry relative="1" as="geometry" />`
- Only services the user asked for (or core path) — do not add IAM/CloudWatch/KMS unless requested
- Never set `background` on `mxGraphModel`

## Output checklist

- File path under `./docs/`
- Companion `.md` path
- Validation status
- Preview / export paths if requested
- Short alt text (<100 chars)
