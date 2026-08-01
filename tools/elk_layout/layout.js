"use strict";

/**
 * Generic ELK layout runner: reads one ELK graph JSON object from stdin
 * (the graph carries its own `layoutOptions` — this script never hardcodes
 * padding/spacing/algorithm choices, those live on the Python side), runs
 * it through elkjs, and writes the laid-out graph JSON to stdout.
 *
 * Used for both "fixed layout, route edges only" graphs and full
 * compound-node layout graphs — the difference is entirely in the graph
 * object's own `layoutOptions`/`algorithm`, not in this script.
 */

const ELK = require("elkjs");

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on("data", (chunk) => chunks.push(chunk));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
}

async function main() {
  const raw = await readStdin();
  let graph;
  try {
    graph = JSON.parse(raw);
  } catch (err) {
    process.stderr.write(`invalid input JSON: ${err.message}\n`);
    process.exit(1);
    return;
  }

  const elk = new ELK();
  try {
    const result = await elk.layout(graph);
    process.stdout.write(JSON.stringify(result));
  } catch (err) {
    process.stderr.write(`elk layout failed: ${err.message || err}\n`);
    process.exit(1);
  }
}

main().catch((err) => {
  process.stderr.write(`unexpected error: ${err.stack || err}\n`);
  process.exit(1);
});
