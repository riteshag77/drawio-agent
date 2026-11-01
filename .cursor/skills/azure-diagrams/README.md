# Azure Diagrams Skill

A comprehensive technical diagramming toolkit for **solutions architects**, **presales engineers**, and **developers**. Generate professional diagrams for proposals, documentation, and architecture reviews.

## What You Can Create

| Diagram Type | Use Case |
|--------------|----------|
| **Azure Architecture** | Solution designs, infrastructure docs |
| **Business Process Flows** | Workflows, approvals, swimlanes |
| **Entity Relationship (ERD)** | Database schemas, data models |
| **Timeline / Gantt** | Project roadmaps, migration plans |
| **UI Wireframes** | Dashboard mockups, screen layouts |
| **Sequence Diagrams** | Auth flows, API interactions |
| **Network Topology** | Hub-spoke, VNets, hybrid cloud |

## Installation

For step-by-step setup with per-OS troubleshooting (Windows/macOS/Linux), see
**[SETUP.md](../../SETUP.md)**. For tool-specific install (Claude Code CLI, GitHub Copilot,
Cursor, etc.), see the [main repository README](https://github.com/cmb211087/azure-diagrams-skill#installation).

### Prerequisites

```bash
# Graphviz (system package), then restart your terminal:
winget install -e --id Graphviz.Graphviz   # Windows  (or: choco install graphviz)
brew install graphviz                       # macOS
sudo apt-get install graphviz               # Linux

# Python library (venv recommended); matplotlib/cairosvg are optional extras:
pip install "diagrams>=0.25.1"
```

## Contents

```
azure-diagrams/
├── SKILL.md                              # Main skill instructions
├── references/
│   ├── azure-components.md               # 800+ Azure components (diagrams 0.25.1)
│   ├── common-patterns.md                # Architecture patterns (SVG default)
│   ├── business-process-flows.md         # Workflow & swimlane patterns
│   ├── entity-relationship-diagrams.md   # ERD patterns
│   ├── timeline-gantt-diagrams.md        # Timeline patterns
│   ├── ui-wireframe-diagrams.md          # Wireframe patterns
│   ├── iac-to-diagram.md                 # Generate from Bicep/Terraform
│   ├── large-diagram-strategies.md       # Handling 50+ node diagrams
│   ├── preventing-overlaps.md            # Layout troubleshooting
│   └── quick-reference.md                # Snippets + WAF patterns
└── scripts/
    ├── generate_diagram.py               # Secure interactive generator
    └── verify_installation.py            # Check prerequisites
```

## Output Format

**PNG is the default and recommended** for sharing (GitHub, email, slides). It is
self-contained and portable.

```python
with Diagram("Title", outformat="png", ...):
```

SVG is optional: it is scalable and smaller, but Graphviz embeds local icon file
paths, so a shared `.svg` shows broken icons on another machine. Install `cairosvg`
(see `requirements-extras.txt`) for self-contained SVG, otherwise prefer PNG.

## Example Prompts

**Architecture Diagram:**
```
Create an e-commerce platform architecture with:
- Front Door for global load balancing
- AKS for microservices
- Cosmos DB for product catalog
- Redis for session cache
- Service Bus for order processing
```

**Business Process Flow:**
```
Create a swimlane diagram for employee onboarding with lanes for:
- HR, IT, Manager, and New Employee
Show the process from offer acceptance to first day completion
```

**ERD Diagram:**
```
Generate an entity relationship diagram for an order management system with:
- Customers, Orders, OrderItems, Products, Categories
- Show primary keys, foreign keys, and cardinality
```

**Well-Architected Pattern:**
```
Create a Zero Trust security architecture showing:
- Entra ID with Conditional Access
- Network micro-segmentation with Firewall
- Private Endpoints for data services
- Key Vault with Managed Identity access
```

**Large Architecture (Split Views):**
```
Create a network view diagram for our 80-resource Azure environment,
focusing only on VNets, subnets, NSGs, and connectivity
```

## Compatibility

| Tool | Status |
|------|--------|
| Claude Code CLI | Supported |
| GitHub Copilot | Supported |
| Cursor | Supported |
| VS Code Copilot | Supported |

Built on the [Agent Skills](https://agentskills.io) open standard.

## License

MIT License - free to use, modify, and distribute.

## Credits

- [diagrams](https://diagrams.mingrammer.com/) - Diagram as Code library
- [Graphviz](https://graphviz.org/) - Graph visualization
- [Agent Skills](https://agentskills.io) - Open standard for AI skills
