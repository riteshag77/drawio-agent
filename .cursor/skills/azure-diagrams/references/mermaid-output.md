# Mermaid Output (zero-install lane)

Mermaid is a text-to-diagram syntax that renders **natively, with no install**, in
GitHub, GitLab, Azure DevOps wikis and Confluence. Use it when the diagram needs to live
inside a Markdown doc, PR, or wiki and be editable there, and when you do not need polished
Azure service icons.

## When to use which

| Need | Use |
|------|-----|
| Polished Azure service icons, PNG for slides/proposals | the `diagrams` + Graphviz pipeline (the rest of this skill) |
| Renders in a GitHub/Azure DevOps/Confluence Markdown doc with zero install | **Mermaid** (this file) |
| Quick edit-in-place flow, sequence, ER or Gantt in a repo doc | **Mermaid** |

Mermaid has **no Azure icon set**, so it is best for flows, sequences, ER and Gantt rather
than icon-rich architecture. Embed any block below in a fenced ` ```mermaid ` block.

## Flowchart (architecture-style)

```mermaid
flowchart LR
    user([User]) --> fd[Front Door]
    fd --> aks[AKS]
    aks --> cosmos[(Cosmos DB)]
    aks --> bus{{Service Bus}}
    bus --> fn[Functions]
    aks -. secrets .-> kv[Key Vault]
```

## Sequence diagram (auth flow)

```mermaid
sequenceDiagram
    actor User
    participant App
    participant Entra as Microsoft Entra ID
    participant API
    User->>App: Open app
    App->>Entra: Redirect to sign in
    Entra-->>App: Authorization code
    App->>Entra: Exchange code for token
    Entra-->>App: Access token
    App->>API: Call API with bearer token
    API-->>App: 200 OK
```

## Entity relationship diagram

```mermaid
erDiagram
    CUSTOMER ||--o{ ORDER : places
    ORDER ||--|{ ORDER_ITEM : contains
    PRODUCT ||--o{ ORDER_ITEM : "ordered in"
    CUSTOMER {
        int id PK
        string email
    }
    ORDER {
        int id PK
        int customer_id FK
        datetime created_at
    }
```

## Gantt (project timeline)

```mermaid
gantt
    title Cloud Migration
    dateFormat YYYY-MM-DD
    section Discovery
    Assessment      :2026-01-01, 14d
    section Build
    Landing zone    :2026-01-15, 21d
    Workload migrate:2026-02-05, 30d
    section Cutover
    Go-live         :2026-03-07, 3d
```

## Architecture-beta (experimental, generic icons only)

Mermaid 11.1+ has an `architecture-beta` diagram with a small generic icon set
(`cloud`, `database`, `disk`, `server`, `internet`). It does not include Azure icons, so
for branded Azure architecture prefer the `diagrams` pipeline.

```mermaid
architecture-beta
    group azure(cloud)[Azure]
    service web(server)[Web App] in azure
    service db(database)[Database] in azure
    service store(disk)[Storage] in azure
    web:R --> L:db
    web:B --> T:store
```
