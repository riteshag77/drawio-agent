# AWS diagrams via Kiro skill + draw.io MCP

This project’s custom layout engine remains for chat/API flows, but **AWS reference-quality diagrams** should use the official skill path below — not `awslabs.aws-diagram-mcp-server` (deprecated / yanked).

## What was installed

| Location | Purpose |
| --- | --- |
| `~/.kiro/skills/aws-architecture-diagram/` | Official skill + references + validation scripts |
| `~/.kiro/skills/deploy/` | Companion deploy skill from `deploy-on-aws` |
| `~/.cursor/skills/aws-architecture-diagram/` | Same skill for Cursor agents |
| `.cursor/skills/aws-architecture-diagram/` | Project-scoped copy |
| `~/.kiro/settings/mcp.json` | Kiro MCP: `drawio-app` + AWS knowledge (no diagram MCP) |
| `~/.cursor/mcp.json` | Cursor MCP: hosted draw.io App Server |
| `.cursor/mcp.json` | Project MCP: `drawio-app` |

## How to generate a diagram

In Cursor or Kiro chat:

```text
Use the aws-architecture-diagram skill to create a multi-AZ three-tier web app
with CloudFront, WAF, ALB, Fargate, Aurora, and CloudWatch.
```

Or with draw.io MCP tools:

```text
Using drawio-app, create an AWS VPC multi-AZ diagram and save to docs/diagrams/arch.drawio
```

Validate / preview locally:

```powershell
python $env:USERPROFILE\.kiro\skills\aws-architecture-diagram\scripts\lib\validate_drawio.py docs\diagrams\your.drawio
python $env:USERPROFILE\.kiro\skills\aws-architecture-diagram\scripts\lib\drawio_url.py docs\diagrams\your.drawio --open
```

## Smoke sample

- File: [docs/diagrams/ha-web-three-tier-sample.drawio](diagrams/ha-web-three-tier-sample.drawio)
- Validated with the skill’s `validate_drawio.py` (PASSED)
- Open in [app.diagrams.net](https://app.diagrams.net) or via the URL helper above

## Decision for `drawio-agent`

**Keep drawio-agent as-is for now** (chat, patterns, multi-cloud JSON → XML).

**Do not** invest further in matching AWS reference layout in `layout.py` / service bands for AWS quality.

**Later (optional):** have the pipeline shell out to this skill or draw.io MCP for AWS `.drawio` output, and keep the local engine for simple / non-AWS diagrams only.

## Do not install

```text
awslabs.aws-diagram-mcp-server   # yanked; superseded by this skill
```

## Azure / GCP / multi-cloud

There is no official GCP/Azure twin of this AWS skill. See **[CLOUD_DIAGRAM_SKILLS.md](CLOUD_DIAGRAM_SKILLS.md)** for the recommended stack (`drawio-skill`, `azure-diagrams`, draw.io MCP, optional `archdiagram`).

## Web app + MCP

Runtime settings and how the FastAPI app uses Draw.io MCP: **[ENV_AND_MCP.md](ENV_AND_MCP.md)** (project `.env`).
