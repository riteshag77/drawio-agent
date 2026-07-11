---
name: azure-diagrams
description: Comprehensive technical diagramming toolkit for solutions architects, presales, and developers. Creates Azure architecture diagrams (800+ Azure service icons), business process flows (swimlanes, workflows), ERD diagrams (database schemas), project timelines, UI wireframes, and network topology diagrams. Perfect for proposals, documentation, and architecture reviews. Also generates diagrams from Bicep, Terraform, and ARM templates.
compatibility: Requires the Graphviz system package and the Python diagrams library (>=0.25.1). Works with Claude Code, GitHub Copilot, Cursor, Windsurf, VS Code, and any Agent Skills or AGENTS.md compatible tool.
license: MIT
metadata:
  author: community
  version: "2.0.0"
  repository: https://github.com/cmb211087/azure-diagrams-skill
---

# Azure Architecture Diagrams Skill

A comprehensive technical diagramming toolkit for solutions architects, presales engineers, and developers. Generate professional diagrams for proposals, documentation, and architecture reviews.

## ⚡ Execution Method

Run the Python directly to generate the image, and keep the image but do not leave a
throwaway `.py` script behind. The generated file is the deliverable.

**Output location (portable).** Write to a `diagrams/` folder in the current working
directory - this works in every tool and on every OS. If you are running in a sandbox that
provides `/mnt/user-data/outputs` (e.g. Claude Code on the web), prefer that. Create the
folder first.

**POSIX shells** (Claude Code, Cursor, Linux/macOS - `bash`/`zsh`):
```bash
mkdir -p diagrams
python3 << 'EOF'
from diagrams import Diagram
from diagrams.azure.compute import AKS
from diagrams.azure.database import CosmosDb

with Diagram("My Architecture", filename="diagrams/architecture", show=False, outformat="png"):
    AKS("aks-prod") >> CosmosDb("cosmos-prod")
EOF
```

**Windows PowerShell** (e.g. GitHub Copilot on Windows - no heredoc): write a short script,
run it, then delete it so nothing is left behind:
```powershell
New-Item -ItemType Directory -Force diagrams > $null
@'
from diagrams import Diagram
from diagrams.azure.compute import AKS
from diagrams.azure.database import CosmosDb
with Diagram("My Architecture", filename="diagrams/architecture", show=False, outformat="png"):
    AKS("aks-prod") >> CosmosDb("cosmos-prod")
'@ | Set-Content _diagram.py; python _diagram.py; Remove-Item _diagram.py
```

**Canned patterns** (cross-platform) - use the bundled generator instead of writing code:
```bash
python scripts/generate_diagram.py --list                       # list available patterns
python scripts/generate_diagram.py --pattern <name> --output diagrams/arch
```

## 📊 Diagram Types

| Type | Reference File | Example Prompt |
|------|----------------|----------------|
| **Azure Architecture** | `references/azure-components.md` | "Design a microservices architecture with AKS and Cosmos DB" |
| **Business Process Flow** | `references/business-process-flows.md` | "Create a swimlane for invoice approval workflow" |
| **Entity Relationship (ERD)** | `references/entity-relationship-diagrams.md` | "Generate an ERD for customer and order entities" |
| **Timeline / Gantt** | `references/timeline-gantt-diagrams.md` | "Create a 6-month migration roadmap" |
| **UI Wireframe** | `references/ui-wireframe-diagrams.md` | "Design a KPI dashboard layout" |
| **Common Patterns** | `references/common-patterns.md` | "Show a hub-spoke network topology" |

## 🔥 Bonus: Generate from Code

Can also create diagrams directly from infrastructure code:

```
Read the Bicep files in /infra and generate an architecture diagram
```

```
Analyze our Terraform modules and create a diagram grouped by subnet
```

```
Read azure-pipelines.yml and create a CI/CD pipeline diagram
```

Supports: **Bicep**, **Terraform**, **ARM Templates**, **Azure Pipelines YAML**, **GitHub Actions**

See `references/iac-to-diagram.md` for detailed prompts and examples.

---

## Prerequisites

```bash
# 1) Graphviz - the rendering engine (system package, not pip). Restart the terminal after.
winget install -e --id Graphviz.Graphviz   # Windows  (or: choco install graphviz)
brew install graphviz                       # macOS
sudo apt-get install -y graphviz            # Linux (Debian/Ubuntu)

# 2) Python library (a virtual environment is recommended):
pip install "diagrams>=0.25.1"
# optional extras: matplotlib (timeline/Gantt/wireframe), cairosvg (portable SVG)
```

## Quick Start

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.compute import FunctionApps, AKS, AppServices
from diagrams.azure.network import ApplicationGateway, LoadBalancers
from diagrams.azure.database import CosmosDb, SQLDatabases, CacheForRedis
from diagrams.azure.storage import BlobStorage
from diagrams.azure.integration import LogicApps, ServiceBus, APIManagement
from diagrams.azure.security import KeyVaults
from diagrams.azure.identity import ActiveDirectory
from diagrams.azure.ml import CognitiveServices

with Diagram("Azure Solution Architecture", show=False, direction="TB"):
    users = ActiveDirectory("Users")
    
    with Cluster("Frontend"):
        gateway = ApplicationGateway("App Gateway")
        web = AppServices("Web App")
    
    with Cluster("Backend"):
        api = APIManagement("API Management")
        functions = FunctionApps("Functions")
        aks = AKS("AKS")
    
    with Cluster("Data"):
        cosmos = CosmosDb("Cosmos DB")
        sql = SQLDatabases("SQL Database")
        redis = CacheForRedis("Redis Cache")
        blob = BlobStorage("Blob Storage")
    
    with Cluster("Integration"):
        bus = ServiceBus("Service Bus")
        logic = LogicApps("Logic Apps")
    
    users >> gateway >> web >> api
    api >> [functions, aks]
    functions >> [cosmos, bus]
    aks >> [sql, redis]
    bus >> logic >> blob
```

## Supported Diagram Types

| Type | Reference File | Use Case |
|------|----------------|----------|
| **Azure Architecture** | `references/azure-components.md` | Cloud infrastructure, solution designs |
| **Common Patterns** | `references/common-patterns.md` | Web apps, microservices, serverless, data platforms |
| **Business Process Flow** | `references/business-process-flows.md` | Workflows, swimlanes, decisions |
| **Entity Relationship (ERD)** | `references/entity-relationship-diagrams.md` | Database schemas, data models |
| **Timeline / Gantt** | `references/timeline-gantt-diagrams.md` | Project plans, roadmaps |
| **UI Wireframe** | `references/ui-wireframe-diagrams.md` | Screen mockups, dashboards |

## Azure Service Categories

| Category | Import | Key Services |
|----------|--------|--------------|
| **Compute** | `diagrams.azure.compute` | VM, AKS, Functions, App Service, Container Apps, Batch |
| **Networking** | `diagrams.azure.network` | VNet, Load Balancer, App Gateway, Front Door, Firewall, ExpressRoute |
| **Database** | `diagrams.azure.database` | SQL, Cosmos DB, PostgreSQL, MySQL, Redis, Synapse |
| **Storage** | `diagrams.azure.storage` | Blob, Files, Data Lake, NetApp, Queue, Table |
| **Integration** | `diagrams.azure.integration` | Logic Apps, Service Bus, Event Grid, APIM, Data Factory |
| **Security** | `diagrams.azure.security` | Key Vault, Sentinel, Defender, Security Center |
| **Identity** | `diagrams.azure.identity` | Entra ID, B2C, Managed Identity, Conditional Access |
| **AI/ML** | `diagrams.azure.ml` | Azure OpenAI, Cognitive Services, ML Workspace, Bot Service |
| **Analytics** | `diagrams.azure.analytics` | Synapse, Databricks, Data Explorer, Stream Analytics, Event Hubs |
| **IoT** | `diagrams.azure.iot` | IoT Hub, IoT Edge, Digital Twins, Time Series Insights |
| **DevOps** | `diagrams.azure.devops` | Azure DevOps, Pipelines, Repos, Boards, Artifacts |
| **Web** | `diagrams.azure.web` | App Service, Static Web Apps, CDN, Media Services |
| **Monitor** | `diagrams.azure.monitor` | Monitor, App Insights, Log Analytics |

See `references/azure-components.md` for the complete list of **800+ components**.

## Common Architecture Patterns

### Web Application (3-Tier)
```python
from diagrams.azure.network import ApplicationGateway
from diagrams.azure.compute import AppServices
from diagrams.azure.database import SQLDatabases

gateway >> AppServices("Web") >> SQLDatabases("DB")
```

### Microservices with AKS
```python
from diagrams.azure.compute import AKS, ACR
from diagrams.azure.network import ApplicationGateway
from diagrams.azure.database import CosmosDb

gateway >> AKS("Cluster") >> CosmosDb("Data")
ACR("Registry") >> AKS("Cluster")
```

### Serverless / Event-Driven
```python
from diagrams.azure.compute import FunctionApps
from diagrams.azure.integration import EventGridTopics, ServiceBus
from diagrams.azure.storage import BlobStorage

EventGridTopics("Events") >> FunctionApps("Process") >> ServiceBus("Queue")
BlobStorage("Trigger") >> FunctionApps("Process")
```

### Data Platform
```python
from diagrams.azure.analytics import DataFactories, Databricks, SynapseAnalytics
from diagrams.azure.storage import DataLakeStorage

DataFactories("Ingest") >> DataLakeStorage("Lake") >> Databricks("Transform") >> SynapseAnalytics("Serve")
```

### Hub-Spoke Networking
```python
from diagrams.azure.network import VirtualNetworks, Firewall, VirtualNetworkGateways

with Cluster("Hub"):
    firewall = Firewall("Firewall")
    vpn = VirtualNetworkGateways("VPN")
    
with Cluster("Spoke 1"):
    spoke1 = VirtualNetworks("Workload 1")
    
spoke1 >> firewall
```

## Connection Syntax

```python
# Basic connections
a >> b                              # Simple arrow
a >> b >> c                         # Chain
a >> [b, c, d]                      # Fan-out (one to many)
[a, b] >> c                         # Fan-in (many to one)

# Labeled connections
a >> Edge(label="HTTPS") >> b       # With label
a >> Edge(label="443") >> b         # Port number

# Styled connections
a >> Edge(style="dashed") >> b      # Dashed line (config/secrets)
a >> Edge(style="dotted") >> b      # Dotted line
a >> Edge(color="red") >> b         # Colored
a >> Edge(color="red", style="bold") >> b  # Combined

# Bidirectional
a >> Edge(label="sync") << b        # Two-way
a - Edge(label="peer") - b          # Undirected
```

## Diagram Attributes

```python
with Diagram(
    "Title",
    show=False,                    # Don't auto-open
    filename="output",             # Output filename (no extension)
    direction="TB",                # TB, BT, LR, RL
    outformat="png",               # png (default, portable), svg, jpg, pdf
    graph_attr={
        "splines": "spline",       # Curved edges
        "nodesep": "1.0",          # Horizontal spacing
        "ranksep": "1.0",          # Vertical spacing
        "pad": "0.5",              # Graph padding
        "bgcolor": "white",        # Background color
    }
):
```

## Clusters (Grouping)

```python
with Cluster("Resource Group"):
    with Cluster("Subnet A"):
        vm1 = VM("VM 1")
        vm2 = VM("VM 2")
    
    with Cluster("Subnet B"):
        db = SQLDatabases("Database")
```

Cluster styling:
```python
with Cluster("Styled", graph_attr={"bgcolor": "#E8F4FD", "style": "rounded"}):
```

## Troubleshooting

### Overlapping Nodes
Increase spacing for complex diagrams:
```python
graph_attr={
    "nodesep": "1.2",   # Horizontal (default 0.25)
    "ranksep": "1.2",   # Vertical (default 0.5)
    "pad": "0.5"
}
```

### Floating Edge Labels
Use `xlabel` instead of `label`:
```python
# Instead of Edge(label="text")
a >> Edge(xlabel="text") >> b
```

### Excessive Whitespace
Compress the output:
```python
graph_attr={"pad": "0.2", "margin": "0", "ratio": "compress"}
```

### Force Horizontal Alignment
Use subgraphs with same rank:
```python
with Diagram(...):
    # These will be on the same horizontal level
    with Cluster("") as same_level:
        same_level.dot.body.append('rank=same')
        a = ServiceA("A")
        b = ServiceB("B")
```

See `references/preventing-overlaps.md` for detailed guidance.

## Output Location

Save to a `diagrams/` folder in the working directory (portable across tools and OSes), or
to `/mnt/user-data/outputs` when running in a sandbox that provides it. Create the folder
first with `mkdir -p diagrams` (POSIX) or `New-Item -ItemType Directory -Force diagrams` (PowerShell).
```python
with Diagram("Name", show=False, filename="diagrams/architecture", outformat="png"):
    # ...
```

## ⚠️ CRITICAL: Professional Output Standards

### The Key Setting: `labelloc='t'`

To keep labels inside cluster boundaries with the `diagrams` library, **put labels ABOVE icons**:

```python
node_attr = {
    "fontname": "Arial Bold",
    "fontsize": "11",
    "labelloc": "t",  # KEY: Labels at TOP - stays inside clusters!
}

with Diagram("Title", node_attr=node_attr, ...):
    # Your diagram code
```

### Full Professional Template

```python
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.compute import AKS
from diagrams.azure.database import SQLDatabases

graph_attr = {
    "bgcolor": "white",
    "pad": "0.8",
    "nodesep": "0.9",
    "ranksep": "0.9",
    "splines": "spline",
    "fontname": "Arial Bold",
    "fontsize": "16",
    "dpi": "200",              # High resolution
}

node_attr = {
    "fontname": "Arial Bold",  # Bold for readability
    "fontsize": "11",
    "labelloc": "t",           # Labels ABOVE icons - KEY!
}

cluster_style = {"margin": "30", "fontname": "Arial Bold", "fontsize": "14"}

with Diagram("My Architecture", 
             direction="TB",
             graph_attr=graph_attr,
             node_attr=node_attr):
    
    with Cluster("Data Tier", graph_attr=cluster_style):
        sql = SQLDatabases("sql-myapp-prod\nS3 tier")
```

### Professional Standards Checklist

| Check | Requirement |
|-------|-------------|
| ✅ **labelloc='t'** | Labels above icons (stays in clusters) |
| ✅ **Bold fonts** | `fontname="Arial Bold"` for readability |
| ✅ **Full resource names** | Actual names from IaC, not abbreviations |
| ✅ **High DPI** | `dpi="200"` for crisp text |
| ✅ **Azure icons** | Use `diagrams.azure.*` components |
| ✅ **Cluster margins** | `margin="30"` or higher |

**⚠️ ALWAYS review the output image before delivering. If ANY text is outside boxes, increase margins or simplify clusters.**

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/generate_diagram.py` | Interactive pattern generator |
| `scripts/multi_diagram_generator.py` | Multi-type diagram generator |
| `scripts/ascii_to_diagram.py` | Convert ASCII diagrams from markdown |
| `scripts/verify_installation.py` | Check prerequisites |
| `scripts/list_azure_nodes.py` | List all Azure icon classes in the installed library |
| `scripts/check_imports.py` | Validate every import in the docs against the library |
| `scripts/render_examples.py` | Execute and verify every runnable example block |

## Reference Files

| File | Content |
|------|---------|
| `references/iac-to-diagram.md` | **Generate diagrams from Bicep/Terraform/ARM** |
| `references/azure-components.md` | Complete list of 800+ Azure components |
| `references/common-patterns.md` | Ready-to-use architecture patterns |
| `references/business-process-flows.md` | Workflow and swimlane diagrams |
| `references/entity-relationship-diagrams.md` | Database ERD patterns |
| `references/timeline-gantt-diagrams.md` | Project timeline diagrams |
| `references/ui-wireframe-diagrams.md` | UI mockup patterns |
| `references/preventing-overlaps.md` | Layout troubleshooting guide |
| `references/sequence-auth-flows.md` | Authentication flow patterns |
| `references/quick-reference.md` | Copy-paste code snippets |
| `references/integration-services.md` | Azure Integration Services component imports |
| `references/large-diagram-strategies.md` | Splitting and C4-style views for large diagrams |
| `references/migration-patterns.md` | Legacy-to-Azure migration scenarios |
| `references/mermaid-output.md` | Zero-install Mermaid diagrams (GitHub / Azure DevOps / Confluence) |
| `references/c4-model.md` | C4 model context / container / component diagrams |
