"use strict";

/**
 * Persistent ELK layout server — the long-lived counterpart to layout.js.
 *
 * layout.js lays out exactly one graph and exits, which costs a full Node
 * start plus an elkjs load (~300 ms measured) for every call, dwarfing the
 * layout itself. This process stays up and answers many requests, paying that
 * cost once.
 *
 * Protocol: newline-delimited JSON, one request per line, one response per
 * line, answered in order.
 *
 *   ->  {"id": 1, "graph": {...}}
 *   <-  {"id": 1, "ok": true,  "result": {...}}
 *   <-  {"id": 1, "ok": false, "error": "..."}
 *
 * The `id` is echoed back so the caller can pair responses with requests.
 * Newline-delimited framing is safe because both sides emit compact JSON,
 * which never contains a raw newline — JSON.stringify escapes them as \n.
 *
 * A malformed line or a failed layout answers with ok:false and the server
 * stays up: one bad graph must not take down layout for every other request.
 */

const ELK = require("elkjs");

const elk = new ELK();

// Requests are processed strictly in order. elkjs is single-threaded anyway,
// so interleaving would buy nothing and would make responses arrive out of
// order relative to a caller that assumes FIFO.
let queue = Promise.resolve();
let buffer = "";

function respond(payload) {
  process.stdout.write(JSON.stringify(payload) + "\n");
}

function handle(line) {
  let request;
  try {
    request = JSON.parse(line);
  } catch (err) {
    respond({ id: null, ok: false, error: `invalid request JSON: ${err.message}` });
    return;
  }

  const id = request && request.id !== undefined ? request.id : null;

  if (!request || typeof request.graph !== "object" || request.graph === null) {
    respond({ id, ok: false, error: "request missing 'graph' object" });
    return;
  }

  queue = queue.then(async () => {
    try {
      const result = await elk.layout(request.graph);
      respond({ id, ok: true, result });
    } catch (err) {
      respond({ id, ok: false, error: `elk layout failed: ${(err && err.message) || err}` });
    }
  });
}

process.stdin.setEncoding("utf8");

process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let newline;
  while ((newline = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, newline);
    buffer = buffer.slice(newline + 1);
    if (line.trim()) handle(line);
  }
});

// Parent closed the pipe (normal shutdown, or the parent died) — finish what
// is already queued, then exit rather than lingering as an orphan.
process.stdin.on("end", () => {
  queue.then(() => process.exit(0));
});

process.on("SIGTERM", () => process.exit(0));
process.on("SIGINT", () => process.exit(0));
