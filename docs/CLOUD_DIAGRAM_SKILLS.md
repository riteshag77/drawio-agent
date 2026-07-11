# Cloud architecture diagram skills — what you need

There is **no official GCP or Azure twin** of AWS’s `aws-architecture-diagram` skill (`awslabs/agent-plugins`). For Azure/GCP/multi-cloud you use community skills + draw.io MCP shape search.

## Already installed (this machine)

| Skill | Scope | Path |
| --- | --- | --- |
| `aws-architecture-diagram` | AWS reference `.drawio` (best AWS quality) | `~/.kiro/skills/`, `.cursor/skills/` |
| `deploy` | AWS deploy / cost companion | `~/.kiro/skills/` |
| `azure-diagrams` | Azure architecture (700+ icons, hub-spoke, IaC) | `~/.kiro/skills/`, `.cursor/skills/` |
| `drawio-skill` | Multi-cloud AWS/Azure/GCP/K8s + IaC→diagram | `~/.kiro/skills/`, `.cursor/skills/` |
| `drawio-app` MCP | Search 10k+ shapes (AWS/Azure/GCP/K8s) + create diagrams | `~/.cursor/mcp.json`, `~/.kiro/settings/mcp.json` |

## Recommended stack for AWS + Azure + GCP + multi-cloud

### Core (install these)

| Priority | Skill / tool | Clouds | Why |
| --- | --- | --- | --- |
| 1 | Keep **`aws-architecture-diagram`** | AWS | Official AWS4 icons, validation, reference style |
| 2 | **[Agents365-ai/drawio-skill](https://github.com/Agents365-ai/drawio-skill)** | AWS / Azure / GCP / K8s | General multi-cloud `.drawio`, IaC→diagram (TF/K8s), shape index |
| 3 | **[jagojar/azure-diagrams-skill](https://github.com/jagojar/azure-diagrams-skill)** | Azure | Deep Azure (700+ icons), hub-spoke, WAF patterns, Bicep/TF |
| 4 | Keep **`drawio-app` MCP** (`https://mcp.draw.io/mcp`) | All | `search_shapes` so icons don’t render as blank boxes |

### Strong optional (multi-cloud render engine)

| Skill | Clouds | Notes |
| --- | --- | --- |
| **[bharathgnana/archdiagram](https://github.com/bharathgnana/archdiagram)** | Azure / AWS / GCP / K8s | Spec → PDF / `.drawio` / VSDX with real vendor icons; skill + MCP |
| **[orossant/Drawio-Kiro-Power](https://github.com/orossant/Drawio-Kiro-Power)** | AWS / Azure / GCP | Kiro Power: XML rules + `cloud-icons.md` for all three |

### GCP specifically

There is **no Google-official** architecture-diagram agent skill today. Practical GCP path:

1. Use **`drawio-skill`** or **`archdiagram`** and say “GCP”
2. Or use **draw.io MCP** `search_shapes` with queries like `gcp cloud run`, `gcp gke`, then `create_diagram`
3. Optional: custom GCP shape library workflow ([GCP + draw.io MCP guide](https://medium.com/google-cloud/automating-mastering-infrastructure-diagrams-with-draw-io-mcp-and-antigravity-2839b78df143))

## How to install (when you want them)

```powershell
# Azure
git clone --depth 1 https://github.com/jagojar/azure-diagrams-skill.git $env:TEMP\azure-diagrams-skill
Copy-Item -Recurse $env:TEMP\azure-diagrams-skill\skills\azure-diagrams $env:USERPROFILE\.cursor\skills\azure-diagrams
Copy-Item -Recurse $env:TEMP\azure-diagrams-skill\skills\azure-diagrams $env:USERPROFILE\.kiro\skills\azure-diagrams

# Multi-cloud draw.io
git clone --depth 1 https://github.com/Agents365-ai/drawio-skill.git $env:TEMP\drawio-skill
Copy-Item -Recurse $env:TEMP\drawio-skill\skills\drawio-skill $env:USERPROFILE\.cursor\skills\drawio-skill
Copy-Item -Recurse $env:TEMP\drawio-skill\skills\drawio-skill $env:USERPROFILE\.kiro\skills\drawio-skill

# Optional: archdiagram (needs python + icon download)
git clone --depth 1 https://github.com/bharathgnana/archdiagram.git $env:TEMP\archdiagram
Copy-Item -Recurse $env:TEMP\archdiagram\skill $env:USERPROFILE\.cursor\skills\architecture-diagrams
```

Then in chat:

```text
Use the azure-diagrams skill for an AKS + Cosmos + Front Door hub-spoke design
Use the drawio-skill for a multi-cloud diagram: CloudFront + Azure Front Door + GCP Cloud CDN
Use drawio-app search_shapes for gcp cloud run before writing XML
```

## Other skills useful for *cloud architecture* work (not only drawing)

| Need | Skill / MCP | Role |
| --- | --- | --- |
| Architecture recommendations + IaC | `deploy` (already from deploy-on-aws) | Propose services, CDK/CFN |
| AWS docs / Well-Architected facts | `awsknowledge` / `aws-documentation` MCP | Correct service choices |
| Cost on the diagram narrative | `aws-pricing` MCP | Rough cost callouts |
| Terraform / IaC accuracy | `awslabs.aws-iac-mcp-server` or drawio-skill TF import | Diagram matches real infra |
| C4 / system context | drawio-skill C4 mode or azure-diagrams C4 refs | Non-icon architecture views |
| Security / Zero Trust views | azure-diagrams WAF patterns; AWS skill security bands | Trust boundaries |
| Sequence / auth flows | azure-diagrams sequence refs; Mermaid via draw.io MCP | Runtime behaviour |
| Microsoft Azure service depth | [Azure Agent Skills (Learn)](https://learn.microsoft.com/en-us/training/support/agent-skills) | Service how-to (not draw.io) |

## What *not* to install

- `awslabs.aws-diagram-mcp-server` — deprecated / yanked
- Multiple overlapping “draw everything” skills without a clear trigger — prefer **one AWS specialist + one Azure specialist + one multi-cloud generalist + draw.io MCP**

## Suggested usage map

```text
AWS-only reference diagram     → aws-architecture-diagram
Azure-only / hub-spoke         → azure-diagrams
GCP-only or multi-cloud canvas → drawio-skill (+ drawio-app search_shapes)
Lucid/Visio handoff            → archdiagram (.pdf / .vsdx)
Icon name unknown              → drawio-app search_shapes first
```

## Decision for drawio-agent

Keep the app for chat/API. For **publishable** cloud diagrams, route by platform to the skills above instead of extending `layout.py` for GCP/Azure parity.

**Runtime (web app):** LLM architecture + Draw.io MCP icon enrichment — see [ENV_AND_MCP.md](ENV_AND_MCP.md).
