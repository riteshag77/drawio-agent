# AWS Architecture Style Guide (LTR / 78px)

Clean left-to-right AWS4 diagrams. **Not** AWS Reference Architecture chrome (no category tint boxes, step badges, sidebar legend, or `light-dark()` fills).

## Title + canvas

1. Background rect first (PNG export): `fillColor=#F5F5F5;strokeColor=none;` covering the page (e.g. 2400×1400).
2. Title text cell: architecture name (14–16px bold Helvetica), optional subtitle.

## Icons

| | Service-level | Resource-level |
|--|---------------|----------------|
| Style | `resourceIcon;resIcon=mxgraph.aws4.*` | `shape=mxgraph.aws4.*` (no resIcon) |
| strokeColor | `#ffffff` (required) | `none` (required) |
| Size | 78×78 (65×65 secondary) | 78×78 or 48×48 |

Category fillColors (examples): Compute `#ED7100`, Networking `#8C4FFF`, Database `#C925D1`, Storage `#3F8624` / `#7AA116`, Security `#DD344C`, Integration `#E7157B`, Management `#E7157B`.

Label: `fontFamily=Helvetica;fontSize=12;fontColor=#232F3E;verticalLabelPosition=bottom;verticalAlign=top;align=center;`

## Groups

- Always `fillColor=none` on Cloud / Account / VPC / subnet / logical groups
- `spacingLeft=40;spacingTop=-4;` with `grIcon` so titles clear the group icon
- `container=1;dropTarget=1;pointerEvents=0;`

## Edges

See SKILL.md Edge section. Prefer `#545B64` or platform orange for primary strokes; dashed for async; red dashed for errors.

## Forbidden (legacy reference-architecture look)

- 48×48 icons inside 120×120 colored category containers
- Teal on-diagram step badges
- Right-sidebar step legend
- `light-dark()` adaptive fills
- Guessing stencil names

## Stencil gotchas

| Service | resIcon / shape |
|---------|-----------------|
| OpenSearch | `elasticsearch_service` |
| EventBridge | `eventbridge` |
| ALB | resource-level `application_load_balancer` |
| NLB | resource-level `network_load_balancer` |

Full lists: `aws4-shapes-services.md`, `aws4-shapes-resources.md`.
