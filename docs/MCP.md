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

## LLM providers

The web UI provider selector supports:

| Provider | Required setup | Optional model override |
|----------|----------------|-------------------------|
| Google Gemini | `GOOGLE_API_KEY` | Built-in `gemini-2.5-flash` |
| OpenAI | `OPENAI_API_KEY` | Built-in `gpt-4o` |
| OpenRouter | `OPENROUTER_API_KEY` | `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL`, `OPENROUTER_STRUCTURED_OUTPUT` |
| Claude | `ANTHROPIC_API_KEY` | Built-in `claude-sonnet-4-6` |
| Cursor | `CURSOR_API_KEY` | `CURSOR_MODEL`, `CURSOR_RUNTIME` |
| OpenCode Zen | `OPENCODE_API_KEY` | `OPENCODE_MODEL` (default `big-pickle`), `OPENCODE_BASE_URL` |

### OpenCode Zen (direct HTTP API)

The OpenCode provider calls [OpenCode Zen](https://opencode.ai/docs/zen/) over HTTP via LangChain `ChatOpenAI`. **No `opencode` CLI install is required.**

**Setup**

1. Create an API key at [opencode.ai/auth](https://opencode.ai/auth).
2. Set `OPENCODE_API_KEY` in `.env` (optional: `OPENCODE_MODEL=big-pickle`).
3. Restart the FastAPI server after changing env vars.

**Supported models**

Only Zen models on the OpenAI-compatible `/chat/completions` endpoint are supported, for example:

- `big-pickle` (free)
- `deepseek-v4-flash-free` (free)
- `mimo-v2.5-free`, `glm-5.2`, `kimi-k2.5`

Claude (`/messages`), GPT 5.x (`/responses`), and Gemini (per-model URLs) are **not** supported by this provider — use the `claude`, `openai`, or `gemini` providers instead.

Model list: `GET https://opencode.ai/zen/v1/models`

**Structured output**

OpenCode Zen uses **text JSON extraction** (plain `invoke` + parse), not LangChain function calling. Tool-based structured output is not used for this provider.

**Troubleshooting**

1. Run `py scripts/diagnose_opencode.py` — lists models and sends a minimal chat prompt.

### OpenRouter structured output

OpenRouter defaults to text JSON extraction (`OPENROUTER_STRUCTURED_OUTPUT=none`) because many routes lack reliable tool calling. To enable function calling or JSON mode on OpenRouter, set:

- `OPENROUTER_STRUCTURED_OUTPUT=function_calling` — LangChain tools (requires model support)
- `OPENROUTER_STRUCTURED_OUTPUT=json_mode` — OpenAI JSON mode

Global `STRUCTURED_OUTPUT_METHOD` still applies to OpenAI, Claude, and Gemini.

### Cursor SDK troubleshooting (local runtime)

The Cursor provider uses the official `cursor-sdk` Python package with **local** runtime by default (`CURSOR_RUNTIME=local`). It runs a bundled Node bridge on your machine against the project directory.

**Setup**

1. Create an API key at [Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations).
2. Set `CURSOR_API_KEY` in `.env` (optional: `CURSOR_MODEL=composer-2.5`).
3. Restart the FastAPI server after changing env vars.

**If you see `Cursor SDK error: internal error`**

1. Enable bridge debug logs: `CURSOR_SDK_LOG=debug` then retry and check the server console.
2. Run the diagnostic script: `py scripts/diagnose_cursor_sdk.py` — lists models and sends a minimal JSON prompt.
3. Check the **Diagram Insights** panel — errors now include `request_id` and `details` from the bridge when available.
4. Confirm `composer-2.5` (or your `CURSOR_MODEL`) appears in `Cursor.models.list()` output.

**Notes**

- The Cursor path uses a **slim system prompt** (not the full `schema.txt` catalogue) to avoid oversized agent prompts on local runtime.
- Local agents use `setting_sources=[]` so ambient IDE settings are not loaded into the SDK service call.

Example:

```json
POST /generate
{
  "user_prompt": "Flowchart: signup → verify email → onboard",
  "diagram_mode": "simple",
  "provider": "openai"
}
```
