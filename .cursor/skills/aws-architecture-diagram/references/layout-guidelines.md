# Layout Guidelines (LTR / 78px)

## Spacing

- **220px** minimum horizontal gap between 78×78 icons (room for edge labels)
- **250px** minimum vertical gap between parallel lanes
- **280px+** gap from main flow down to auxiliary / monitoring / DLQ
- Align to grid multiples of 10
- Page: at least `pageWidth=2400` `pageHeight=1400`; viewport `dx=2800` `dy=1600`

## Flow direction

- Primary path: **left → right**
- Users / clients on the **left**
- Databases / lakes / external systems on the **right**
- Use horizontal lanes for parallel paths (top lane / bottom lane)
- Nest only real boundaries (cloud → region → VPC → subnet); keep services in flat rows inside

## Edge routing

- `edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;`
- Default LTR anchors: `exitX=1;exitY=0.5` → `entryX=0;entryY=0.5`
- To services above/below: use top/bottom exits (`exitY=0` / `1`, `entryY=1` / `0`) so lines do not cut through icons
- Spread multiple exits/entries across the face (0.25 / 0.5 / 0.75)
- Add waypoints only when routing around intervening clusters
- Cross-container edges: `parent="1"`

## Labels

- 1–2 words; `labelBackgroundColor=#F5F5F5;fontSize=11`
- Omit `value` when there is no label
- Do not label obvious hops

## What not to do

- Do not use 180/120 spacing tuned for 120×120 category boxes
- Do not place a right-sidebar legend or on-diagram step badges
- Do not pack vertical “reference architecture columns” as the default; prefer LTR lanes
