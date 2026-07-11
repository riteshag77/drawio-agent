# Eval scenarios

## Triggering scenarios

These prompts SHOULD activate the skill:

- "Design a microservices architecture with AKS and Cosmos DB"
- "Draw a diagram of the Bicep in ./infra"
- "Create a swimlane for the invoice approval workflow"
- "Visualise this system as a hub-spoke network"
- "Generate an ERD for customer and order entities"
- "Make a 6-month migration timeline"

These should NOT activate it (negative cases):

- "Explain what AKS is" (no diagram intent)
- "Refactor this Python function" (unrelated)

If a description change regresses any of these, do not ship it.

## Task scenarios

### 1. Diagram from IaC (deterministic)

**Prompt:** "Diagram the infrastructure in `tests/fixtures/sample-arm.json`."
**Expected:** uses `scripts/iac_to_diagram.py` (or equivalent), producing a PNG with
AppServices, CosmosDb, ServiceBus, KeyVaults, VirtualNetworks, ApplicationInsights and the
`dependsOn` edges (plan -> api, plan -> worker, bus -> worker, api -> appinsights).
**Pass:** a PNG is written under `diagrams/`; all 8 resources appear; no ImportError.

### 2. Swimlane for invoice approval

**Prompt:** "Create a swimlane diagram for an invoice approval workflow."
**Expected:** a graphviz/diagrams swimlane with distinct actor lanes (e.g. Submitter,
Approver, Finance) and steps connected across lanes; renders to an image.
**Pass:** renders without error; has >= 2 distinct lanes; uses neutral/fictional names only.

### 3. Hub-spoke topology

**Prompt:** "Create a hub-spoke network with a hub VNet and two spokes."
**Expected:** follows the hub-spoke pattern in `references/common-patterns.md`; `Bastions`
imported from `diagrams.azure.networking`; PNG output.
**Pass:** renders; shows a hub plus two spokes with a firewall and bastion; default PNG.

### 4. Cross-tool / portability

**Prompt (on Windows, e.g. Copilot):** "Generate a simple Azure web app diagram."
**Expected:** uses the Windows PowerShell execution path (no bash heredoc), writes to
`diagrams/`, and does not assume `/mnt/user-data/outputs`.
**Pass:** a PNG is produced on Windows with no path or shell error.
