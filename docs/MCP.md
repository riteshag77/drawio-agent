# Draw.io MCP (dev-only)

Optional [official draw.io MCP server](https://www.drawio.com/docs/manual/generate/drawio-mcp-server/) for ad-hoc diagrams while developing in Cursor. **Not used by the FastAPI app at runtime.**

## When to use

| Scenario | Use |
|----------|-----|
| Cloud architecture (AWS/GCP/Azure icons, VPC, validation) | This app's **JSON → draftsman** pipeline (`POST /chat`, `POST /generate`) |
| Simple flowchart / sequence / org chart | App **Mermaid fast path** (auto-detected or `diagram_mode: "simple"`) |
| One-off diagram in Cursor chat, open in browser | `@drawio/mcp` below |

## Cursor MCP config

Add to Cursor MCP settings (or `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "drawio": {
      "command": "npx",
      "args": ["-y", "@drawio/mcp"]
    }
  }
}
```

Restart Cursor, then try:

> Draw a Mermaid sequence diagram for OAuth login (Client, Auth Server, Resource Server) and open it in draw.io.

## Tools exposed by `@drawio/mcp`

- `open_drawio_xml` — native draw.io XML
- `open_drawio_csv` — tabular → org chart / flowchart
- `open_drawio_mermaid` — Mermaid.js → editable diagram

These return a draw.io URL (browser tab). The web UI embed uses Mermaid **import** postMessage instead of spawning Node per request.

## API diagram modes

Use the **Diagram type** selector in the web UI (Auto / Cloud / Simple), or pass `diagram_mode` on every `/chat` and `/generate` request.

| Mode | `diagram_mode` | When to use |
|------|----------------|-------------|
| Auto (default) | `auto` | Let the server classify: simple keywords → Mermaid; cloud keywords → JSON pipeline |
| Cloud | `cloud` | AWS/GCP/Azure architecture with icons, VPC containers, validation |
| Simple (Mermaid) | `simple` | Flowcharts, sequence diagrams, org charts without cloud icons |

Cloud diagrams also use **Auto-layout on load** (Draw.io `horizontalFlow`) in the embed unless disabled in Settings.

Example:

```json
POST /generate
{
  "user_prompt": "Flowchart: signup → verify email → onboard",
  "diagram_mode": "simple",
  "provider": "openai"
}
```
