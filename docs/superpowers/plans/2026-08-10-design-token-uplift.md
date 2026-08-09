# Design Token Uplift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make corner radius, spacing, elevation, icon color behavior, and label
typography consistent across the whole app (all three themes + admin.html) by
introducing a real design-token layer in `static/styles.css`, replacing today's
scattered ad hoc values.

**Architecture:** `static/styles.css` already separates structure (its own file)
from color (per-theme files under `static/themes/`). This plan adds a token layer
of CSS custom properties for spacing/radius/type/elevation to `styles.css`'s
existing `:root` block, then migrates existing hardcoded literals onto those
tokens selector-by-selector, in staged, independently-verifiable groups. No HTML
or JS changes. No color changes. No new components.

**Tech Stack:** Plain CSS custom properties, no build step. Verification is
visual (Playwright screenshots), since this is a pure CSS project with no
existing visual-regression test harness.

## Global Constraints

- No HTML markup changes, no JS changes, no new components, no behavior changes.
- `daylight` and `midnight-slate` keep their own colors; only shape/spacing/type/
  icon-behavior becomes consistent across all three themes.
- Every task must be verified by screenshotting `midnight-slate`, `daylight`, and
  `aurora-contrast` (Playwright, Python already installed in this repo's venv)
  before moving to the next task — no bulk unverified passes.
- The dev server (`python run.py`) must already be running at
  `http://localhost:8000/` for the screenshot steps. Check with:
  `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/` (expect a
  redirect/200, not a connection error) before running any screenshot step —
  if it's not running, start it with `python run.py` in the background first.
- Spec: `docs/superpowers/specs/2026-08-10-design-token-uplift-design.md`

---

## Verification helper (used by every task below)

Every task's "Verify" step runs this Python snippet, changing only the
`THEME` value and, where noted, the interaction (e.g. clicking `.settings-trigger`).
Save it to the scratchpad once and reuse it — do not commit it to the repo.

```python
# scratchpad_shot.py — usage: python scratchpad_shot.py <theme> <outfile> [--settings]
import sys, time
from playwright.sync_api import sync_playwright

theme, outfile = sys.argv[1], sys.argv[2]
open_settings = "--settings" in sys.argv

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto("http://localhost:8000/", wait_until="networkidle", timeout=15000)
    page.evaluate(
        f"document.documentElement.setAttribute('data-theme', '{theme}');"
        f"try {{ localStorage.setItem('drawio-agent-app-theme','{theme}'); }} catch(e){{}}"
    )
    if open_settings:
        page.click(".settings-trigger")
    time.sleep(0.5)
    page.screenshot(path=outfile, full_page=False)
    browser.close()
print("done")
```

Run for all three themes after each task:
```bash
python scratchpad_shot.py midnight-slate midnight-slate.png
python scratchpad_shot.py daylight daylight.png
python scratchpad_shot.py aurora-contrast aurora-contrast.png
```
Then Read each PNG and visually confirm: no overflow/clipping, no lost contrast,
the changed property actually changed, nothing unrelated moved.

---

### Task 1: Add the token layer + migrate the three modal dialogs and two focus rings

**Files:**
- Modify: `static/styles.css:1-9` (root token block)
- Modify: `static/styles.css:3561-3562` (`.history-dialog`)
- Modify: `static/styles.css:1337-1338` (`.provider-picker-popover`)
- Modify: `static/styles.css:3701-3702` (`.provider-config-dialog`)
- Modify: `static/styles.css:1211-1213` (`.composer:focus-within`)
- Modify: `static/styles.css:1929-1937` (shared `textarea:focus, select:focus, ...` group)

**Interfaces:**
- Produces: `--space-1`..`--space-8`, `--radius-sm`, `--radius-md`, `--radius-lg`,
  `--radius-pill`, `--font-sans`, `--font-mono`, `--shadow-focus`,
  `--shadow-panel`, `--transition-fast`, `--transition-panel` — every later task
  in this plan consumes these exact names.

- [ ] **Step 1: Replace the root token block**

Current (`static/styles.css:1-9`):
```css
        /* Layout-only tokens shared by every theme. Color/surface tokens are
           defined per-theme in /static/themes/*.css under :root[data-theme="..."]. */
        :root {
            --radius: 12px;
            --sidebar-width: 440px;
            --sidebar-min-width: 320px;
            --sidebar-max-width: 800px;
            --log-panel-width: 420px;
        }
```

Replace with:
```css
        /* Layout-only tokens shared by every theme. Color/surface tokens are
           defined per-theme in /static/themes/*.css under :root[data-theme="..."]. */
        :root {
            --sidebar-width: 440px;
            --sidebar-min-width: 320px;
            --sidebar-max-width: 800px;
            --log-panel-width: 420px;

            /* spacing (4px base grid) */
            --space-1: 0.25rem;
            --space-2: 0.5rem;
            --space-3: 0.75rem;
            --space-4: 1rem;
            --space-5: 1.25rem;
            --space-6: 1.5rem;
            --space-8: 2rem;

            /* radius */
            --radius-sm: 6px;
            --radius-md: 8px;
            --radius-lg: 12px;
            --radius-pill: 999px;

            /* type */
            --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            --font-mono: ui-monospace, 'SFMono-Regular', 'JetBrains Mono', Menlo, monospace;

            /* elevation */
            --shadow-focus: 0 0 0 3px var(--accent-glow);
            --shadow-panel: 0 24px 60px rgba(0,0,0,0.45);
            --transition-fast: 120ms ease;
            --transition-panel: 180ms ease;
        }
```

- [ ] **Step 2: Migrate the three modal dialogs**

`.history-dialog` (`static/styles.css:3561-3562`) — change:
```css
    border-radius: var(--radius);
    box-shadow: 0 24px 60px rgba(0,0,0,0.45);
```
to:
```css
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-panel);
```

`.provider-picker-popover` (`static/styles.css:1337-1338`) — change:
```css
            border-radius: 14px;
            box-shadow: 0 24px 60px rgba(0,0,0,0.45);
```
to:
```css
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow-panel);
```

`.provider-config-dialog` (`static/styles.css:3701-3702`) — change:
```css
    border-radius: 14px;
    box-shadow: 0 24px 60px rgba(0,0,0,0.45);
```
to:
```css
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-panel);
```

- [ ] **Step 3: Migrate the two focus rings**

`.composer:focus-within` (`static/styles.css:1211-1213`) — change:
```css
        .composer:focus-within {
            border-color: var(--accent);
            box-shadow: 0 0 0 3px var(--accent-glow);
        }
```
to:
```css
        .composer:focus-within {
            border-color: var(--accent);
            box-shadow: var(--shadow-focus);
        }
```

Shared input/textarea/select focus group (`static/styles.css:1929-1937`) — change:
```css
        textarea:focus, select:focus,
        .drawer-group-body input[type="text"]:focus,
        .drawer-group-body input[type="password"]:focus,
        .drawer-group-body input[type="url"]:focus,
        .provider-config-detail input[type="text"]:focus,
        .provider-config-detail input[type="password"]:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px var(--accent-glow);
        }
```
to the same block with the last line changed to `box-shadow: var(--shadow-focus);`.

- [ ] **Step 4: Verify**

Confirm the dev server is running, then run the verification helper for all
three themes (main view only, no `--settings` needed since none of these
selectors show on the default screen except via opening History or a
provider dialog — for this task it's enough to confirm the page still loads
with no visual regression on the default view, since these are purely
value substitutions with identical computed output — `var(--radius-lg)` is
still `12px`, `var(--shadow-panel)` is still `0 24px 60px rgba(0,0,0,0.45)`).

Run: `python scratchpad_shot.py midnight-slate t1-midnight.png`
Run: `python scratchpad_shot.py daylight t1-daylight.png`
Run: `python scratchpad_shot.py aurora-contrast t1-aurora.png`

Expected: all three load with no console errors and an unchanged default
view (this task only swaps hardcoded values for tokens with the same
resolved value — the check confirms nothing typo'd/broke, not a visual
diff).

- [ ] **Step 5: Commit**

```bash
git add static/styles.css
git commit -m "feat: add spacing/radius/type/elevation design tokens"
```

---

### Task 2: Migrate sidebar chrome and primary controls onto the new tokens

**Files:**
- Modify: `static/styles.css:185-190` (`.sidebar-header`)
- Modify: `static/styles.css:510-516` (`.chat-toolbar`)
- Modify: `static/styles.css:198-209` (`.brand-mark`)
- Modify: `static/styles.css:1787-1792` (`.btn-send`)
- Modify: `static/styles.css:582-592` (`.btn-chat-clear`)

**Interfaces:**
- Consumes: `--space-5`, `--space-6`, `--space-4`, `--radius-sm`, `--radius-md`
  (from Task 1).

- [ ] **Step 1: Sidebar header + chat toolbar padding**

`.sidebar-header` (`static/styles.css:185-190`) — change:
```css
        .sidebar-header {
            padding: 0.85rem 1.15rem;
```
to:
```css
        .sidebar-header {
            padding: var(--space-5) var(--space-6);
```
(keep the rest of the rule — the `border-bottom` and `background` lines — unchanged)

`.chat-toolbar` (`static/styles.css:510-516`) — change:
```css
        .chat-toolbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.65rem 1.5rem;
```
to:
```css
        .chat-toolbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: var(--space-4) var(--space-6);
```

- [ ] **Step 2: Brand mark and send button radius**

`.brand-mark` (`static/styles.css:198-209`) — change `border-radius: 9px;` to
`border-radius: var(--radius-md);`.

`.btn-send` (`static/styles.css:1787-1792`) — change `border-radius: 10px;` to
`border-radius: var(--radius-md);`.

`.btn-chat-clear` (`static/styles.css:582-592`) — change `border-radius: 6px;`
to `border-radius: var(--radius-sm);` (same resolved value, tokenized for
consistency/maintainability).

- [ ] **Step 3: Verify**

Run the verification helper for all three themes, main view (no `--settings`
flag). Confirm: sidebar header has slightly more breathing room, chat toolbar
has slightly more vertical padding, brand icon and send button corners read
consistently with other buttons — no text clipping, no overflow.

- [ ] **Step 4: Commit**

```bash
git add static/styles.css
git commit -m "feat: migrate sidebar chrome spacing/radius onto design tokens"
```

---

### Task 3: Gallery cards, segmented control, and icon-color rule

**Files:**
- Modify: `static/styles.css:652-656` (`.gallery-grid`)
- Modify: `static/styles.css:658-670` (`.gallery-card`)
- Modify: `static/styles.css:682-688` (`.gallery-card-icon`) — add hover rule
- Modify: `static/styles.css:736-750` (`.example-chip`, radius only)
- Modify: `static/styles.css:252-292` (`.segmented` + `.platform-card` inside it)
- Modify: `static/styles.css:1003-1010` (`.chat-suggestions`)

**Interfaces:**
- Consumes: `--space-3`, `--space-4`, `--radius-sm`, `--radius-md` (Task 1).
- Produces: the icon-color convention (`.gallery-card-icon` resting on
  `var(--text-secondary)`, `var(--accent)` on hover) that Task 8 reuses for
  other icon selectors.

- [ ] **Step 1: Gallery grid/card spacing and radius**

`.gallery-grid` (`static/styles.css:652-656`) — change `gap: 0.5rem;` to
`gap: var(--space-3);`.

`.gallery-card` (`static/styles.css:658-670`) — change:
```css
        .gallery-card {
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 0.35rem;
            padding: 0.7rem 0.75rem;
            border-radius: 10px;
```
to:
```css
        .gallery-card {
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: var(--space-2);
            padding: var(--space-4);
            border-radius: var(--radius-md);
```

`.example-chip` (`static/styles.css:736-750`, the `border-radius: 10px;` line
only) — change to `border-radius: var(--radius-md);`.

- [ ] **Step 2: Icon-color rule on gallery cards**

`.gallery-card-icon` (`static/styles.css:682-688`) — change:
```css
        .gallery-card-icon {
            width: 28px;
            height: 28px;
            flex-shrink: 0;
            color: var(--accent);
            opacity: 0.85;
        }
```
to:
```css
        .gallery-card-icon {
            width: 28px;
            height: 28px;
            flex-shrink: 0;
            color: var(--text-secondary);
            opacity: 0.85;
            transition: color var(--transition-fast);
        }

        .gallery-card:hover .gallery-card-icon {
            color: var(--accent);
        }
```

- [ ] **Step 3: Segmented control radius**

`.segmented` (around `static/styles.css:252-255`, the wrapper with
`padding: 4px; background: var(--surface-well); border-radius: 11px;`) —
change `border-radius: 11px;` to `border-radius: var(--radius-md);`.

`.segmented .platform-card` (around `static/styles.css:263-266`, inner button
with `border-radius: 8px;`) — change to `border-radius: var(--radius-sm);`.

`.segmented .platform-card.active` (`static/styles.css:282-285`) — remove the
`box-shadow: 0 2px 8px var(--accent-glow);` line entirely (keep the
`background: linear-gradient(...)` line) — the active tab still reads clearly
via its filled gradient background; this drops one of the decorative glow
blooms per the flat/Linear-style elevation goal.

- [ ] **Step 4: Chat suggestions radius**

`.chat-suggestions` (`static/styles.css:1003-1010`, the
`border-radius: 10px;` line) — change to `border-radius: var(--radius-md);`.

- [ ] **Step 5: Verify**

Run the verification helper for all three themes, main view. Confirm:
pattern-card icons are now neutral gray at rest (not colored), gallery cards
have slightly more gap/padding, segmented control (AWS/GCP/Azure/Multi) still
reads with AWS clearly active (via its filled background, no glow), no
layout overflow in the 2-column gallery grid.

Additionally hover over a gallery card in a real browser (not just the
screenshot) to confirm the icon turns to the theme's accent color on hover —
screenshots can't capture `:hover`, so this one check needs a live look, not
just the PNG.

- [ ] **Step 6: Commit**

```bash
git add static/styles.css
git commit -m "feat: migrate gallery/segmented controls to design tokens, add icon-color rule"
```

---

### Task 4: Chat bubbles and shared form-control radius

**Files:**
- Modify: `static/styles.css:770-773` (`.chat-bubble`)
- Modify: `static/styles.css:1166-1169` (second chat-bubble-style selector)
- Modify: `static/styles.css:1865-1868` (`.platform-card`)
- Modify: `static/styles.css:1895-1904` (shared textarea/select/input radius)
- Modify: `static/styles.css:1653-1655` (`.provider-card`)

**Interfaces:**
- Consumes: `--radius-lg`, `--radius-md` (Task 1).

- [ ] **Step 1: Chat bubble radius (both occurrences)**

`.chat-bubble` (`static/styles.css:770-773`) — change `border-radius: 14px;`
to `border-radius: var(--radius-lg);`.

Second occurrence (`static/styles.css:1166-1169`, the
`padding: 0.75rem 1rem; border-radius: 14px;` block) — change
`border-radius: 14px;` to `border-radius: var(--radius-lg);`.

- [ ] **Step 2: Platform card and shared input radius**

`.platform-card` (`static/styles.css:1865-1868`) — change
`border-radius: 10px;` to `border-radius: var(--radius-md);`.

Shared textarea/select/input group (`static/styles.css:1895-1904`, the
`border-radius: 10px;` line inside the multi-selector block starting
`textarea, select, .drawer-group-body input[type="text"], ...`) — change to
`border-radius: var(--radius-md);`.

`.provider-card` (`static/styles.css:1653-1655`) — change
`border-radius: 10px;` to `border-radius: var(--radius-md);`.

- [ ] **Step 3: Verify**

Run the verification helper for all three themes, both main view and
`--settings` (to see form inputs in the settings drawer). Confirm chat
bubbles, the AWS/GCP/Azure/Multi platform cards, and text inputs/selects all
read with the same corner radius now.

- [ ] **Step 4: Commit**

```bash
git add static/styles.css
git commit -m "feat: migrate chat bubbles and form controls to design tokens"
```

---

### Task 5: Eyebrow-label weight unification (Phase 1 set)

**Files:**
- Modify: `static/styles.css` at each selector listed below (font-weight only).

**Interfaces:**
- None (pure `font-weight` change, no new tokens produced/consumed beyond
  what Task 1 already added).

- [ ] **Step 1: Drop these seven selectors from `font-weight: 700` (or 600) to `500`**

Each of these currently sets `font-weight: 700;` (or `600;` for
`.chat-toolbar span`) alongside `text-transform: uppercase;` — change only
the `font-weight` line to `500` in each, leaving `font-size`,
`letter-spacing`, `color`, and everything else untouched:

- `.gallery-header h3` — `static/styles.css:637-644`
- `.chat-toolbar span` — `static/styles.css:519-524` (currently `600`)
- `.drawer-group-title` — `static/styles.css:385-389`
- `.chat-suggestions-title` — `static/styles.css:1011-1015`
- `.suggestion-applied-badge` — `static/styles.css:1076-1082`
- `.gallery-badge` — `static/styles.css:719-728`
- `.collapsed-rail-label` — `static/styles.css:140-146`

- [ ] **Step 2: Verify**

Run the verification helper for all three themes, main view (shows
`.gallery-header h3` "ARCHITECTURE PATTERNS" and `.chat-toolbar span`
"CONVERSATION" directly). Confirm the labels read lighter/more refined, still
legible, no contrast complaints (weight change doesn't affect color/contrast
ratios, so no dataviz re-check needed here).

- [ ] **Step 3: Commit**

```bash
git add static/styles.css
git commit -m "style: lighten eyebrow-label weight for a less bold, more refined rhythm"
```

---

### Task 6: Clean up `aurora-contrast.css` — remove now-redundant theme-local overrides

**Files:**
- Modify: `static/themes/aurora-contrast.css`

**Interfaces:**
- Consumes: everything Tasks 1-5 already put in `static/styles.css` globally —
  this task only removes duplication, it adds no new behavior.

- [ ] **Step 1: Remove the v3 structural overrides that are now redundant**

Open `static/themes/aurora-contrast.css`. Remove these rules entirely (they
duplicate what Tasks 1-5 now provide globally):

```css
:root[data-theme="aurora-contrast"] .brand-text h1 {
    font-family: Georgia, 'Iowan Old Style', 'Palatino Linotype', ui-serif, serif;
    letter-spacing: 0.01em;
}

:root[data-theme="aurora-contrast"] .gallery-header p {
    font-style: italic;
    font-family: Georgia, 'Iowan Old Style', 'Palatino Linotype', ui-serif, serif;
}

:root[data-theme="aurora-contrast"] .gallery-header h3,
:root[data-theme="aurora-contrast"] .chat-toolbar span,
:root[data-theme="aurora-contrast"] .chat-suggestions-title,
:root[data-theme="aurora-contrast"] .suggestions-group-header,
:root[data-theme="aurora-contrast"] .drawer-group-title,
:root[data-theme="aurora-contrast"] .cost-category-name,
:root[data-theme="aurora-contrast"] .compliance-scope-title,
:root[data-theme="aurora-contrast"] .cost-donut-center-label,
:root[data-theme="aurora-contrast"] .token-meter-label,
:root[data-theme="aurora-contrast"] .governance-item-scope,
:root[data-theme="aurora-contrast"] .gallery-badge,
:root[data-theme="aurora-contrast"] .log-step {
    font-weight: 500;
    letter-spacing: 0.09em;
}
```

and:

```css
:root[data-theme="aurora-contrast"] .gallery-card,
:root[data-theme="aurora-contrast"] .example-chip,
:root[data-theme="aurora-contrast"] .platform-card,
:root[data-theme="aurora-contrast"] .segmented .platform-card,
:root[data-theme="aurora-contrast"] .chat-suggestions,
:root[data-theme="aurora-contrast"] .provider-card,
:root[data-theme="aurora-contrast"] .chat-bubble {
    border-radius: 4px;
}

:root[data-theme="aurora-contrast"] .composer {
    border-radius: 6px;
}

:root[data-theme="aurora-contrast"] .segmented {
    border-radius: 5px;
}

:root[data-theme="aurora-contrast"] .sidebar-header {
    padding: 1.1rem 1.3rem;
}

:root[data-theme="aurora-contrast"] .gallery-grid {
    gap: 0.65rem;
}

:root[data-theme="aurora-contrast"] .gallery-card {
    padding: 0.9rem 1rem;
    gap: 0.45rem;
}

:root[data-theme="aurora-contrast"] .chat-toolbar {
    padding: 0.8rem 1.5rem;
}
```

- [ ] **Step 2: Keep the color-specific rules — verify these remain untouched**

Confirm the following are still present in the file after Step 1 (they are
color decisions, not structural ones, and stay theme-local):

```css
:root[data-theme="aurora-contrast"] .btn-action.primary,
:root[data-theme="aurora-contrast"] .suggestion-btn-primary,
:root[data-theme="aurora-contrast"] .segmented .platform-card.active {
    font-weight: 600;
}

:root[data-theme="aurora-contrast"] .gallery-card-icon {
    color: var(--text-secondary);
    transition: color 0.15s;
}

:root[data-theme="aurora-contrast"] .gallery-card:hover .gallery-card-icon {
    color: var(--accent);
}

:root[data-theme="aurora-contrast"] .settings-drawer {
    background: #0f0f13;
    border-left: 1px solid var(--border-strong);
}

:root[data-theme="aurora-contrast"] .gallery-card,
:root[data-theme="aurora-contrast"] .example-chip {
    background: #0f0f13;
    border-color: var(--border-subtle);
}

:root[data-theme="aurora-contrast"] .chat-message.assistant .chat-bubble {
    background: #14151c;
}
```

Note the `.gallery-card-icon` color rule here now duplicates the global one
from Task 3 Step 2 in effect (both set resting to `text-secondary`, hover to
`accent`) — that's fine, this one is redundant-but-harmless since it sets the
exact same values; leave it in place rather than risk deleting something a
future theme-specific icon color divergence might want to hook into. Do not
delete it.

- [ ] **Step 3: Bump the cache-bust query param**

`static/index.html` and `static/admin.html` both have:
```html
    <link rel="stylesheet" href="/static/themes/aurora-contrast.css?v=20260810c">
```
Change `20260810c` to `20260810d` in both files.

- [ ] **Step 4: Verify**

Run the verification helper for `aurora-contrast` only, main view and
`--settings`. Confirm the theme now looks identical in spacing/radius/type to
what Tasks 1-5 established globally (since it now inherits rather than
overriding), and that the serif experiment is gone (wordmark and gallery
subtitle are back to `--font-sans`/Inter).

- [ ] **Step 5: Commit**

```bash
git add static/themes/aurora-contrast.css static/index.html static/admin.html
git commit -m "refactor: remove aurora-contrast overrides superseded by global design tokens"
```

---

### Task 7: Phase 2 — cost/token numeric readouts switch to monospace

**Files:**
- Modify: `static/styles.css:2158-2162` (`.cost-total-amount`)
- Modify: `static/styles.css:2207-2211` (`.cost-donut-center-amount`)
- Modify: `static/styles.css:2984-2990` (`.token-meter-summary`)

**Interfaces:**
- Consumes: `--font-mono` (Task 1).

- [ ] **Step 1: Add `font-family: var(--font-mono);` to each of the three selectors**

`.cost-total-amount` (`static/styles.css:2158-2162`):
```css
        .cost-total-amount {
            font-size: 1.6rem;
            font-weight: 900;
            color: var(--text-primary);
            letter-spacing: -0.04em;
            font-family: var(--font-mono);
        }
```

`.cost-donut-center-amount` (`static/styles.css:2207-2211`):
```css
        .cost-donut-center-amount {
            font-size: 1.15rem;
            font-weight: 800;
            color: var(--text-primary);
            letter-spacing: -0.03em;
            font-family: var(--font-mono);
        }
```

`.token-meter-summary` (`static/styles.css:2984-2990`):
```css
        .token-meter-summary {
            font-size: 0.75rem;
            font-weight: 600;
            color: var(--canvas-text, var(--text-primary, #0f172a));
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
            font-family: var(--font-mono);
        }
```

- [ ] **Step 2: Verify**

Run the verification helper for all three themes with `--settings` (the cost
panel and token meter aren't on the bare default screen — if they require
generating a diagram first, it's enough to open Settings and confirm no CSS
error broke the page; do a follow-up manual check in a live browser session
generating a diagram to see the cost figures and token meter render in
monospace, since this is easier to eyeball live than to script open flows for
a one-off check).

- [ ] **Step 3: Commit**

```bash
git add static/styles.css
git commit -m "style: monospace numeric cost/token readouts for a more precise, instrument-panel feel"
```

---

### Task 8: Phase 2 — history cards, remaining radii, remaining eyebrow labels

**Files:**
- Modify: `static/styles.css:2951-2957` (`.token-meter`)
- Modify: `static/styles.css:3585-3597` (`.history-card`, radius + hover shadow removal)
- Modify: eyebrow-label selectors listed in Step 3 below (font-weight only)

**Interfaces:**
- Consumes: `--radius-md`, `--radius-lg` (Task 1).

- [ ] **Step 1: Token meter radius**

`.token-meter` (`static/styles.css:2951-2957`) — change `border-radius: 10px;`
to `border-radius: var(--radius-md);`.

- [ ] **Step 2: History card radius and flatten the hover lift/glow**

`.history-card` (around `static/styles.css:3585-3597`) currently has
`border-radius: 12px;` on the base rule and, on `:hover`, both a `translateY(-2px)`
lift and a `box-shadow: 0 8px 24px -8px var(--accent-glow);` bloom. Change:
- base rule: `border-radius: 12px;` → `border-radius: var(--radius-lg);`
- `.history-card:hover` block — remove the `transform: translateY(-2px);` and
  `box-shadow: 0 8px 24px -8px var(--accent-glow);` lines, keeping only
  `border-color: var(--accent);`. This matches `.gallery-card:hover`'s flatter
  treatment (border/background change only, no lift-and-glow) established in
  Task 3.

Do not change `.history-card-current`'s `box-shadow: 0 0 0 1px var(--accent);`
— that's a functional selection ring, not a decorative hover bloom.

- [ ] **Step 3: Remaining eyebrow-label weight unification**

Change `font-weight` to `500` (leaving size/spacing/color untouched) on:

- `.validation-issue-badge` — `static/styles.css:883-888`
- `.provider-picker-group-label` — `static/styles.css:1388-1393`
- `.provider-picker-item-untested` — `static/styles.css:1436-1441`
- `label` (generic form label) — `static/styles.css:1849-1855`
- `.suggestions-group-header` — `static/styles.css:2057-2061`
- `.suggestion-category-chip` — `static/styles.css:2078-2084`
- `.cost-donut-center-label` — `static/styles.css:2215-2220`
- `.cost-category-name` — `static/styles.css:2311-2315`
- `.token-meter-label` — `static/styles.css:2976-2982`
- `.compliance-scope-title` — `static/styles.css:3398-3403`
- `.governance-item-scope` — `static/styles.css:2559` (inline rule; change the
  `font-weight` — note this one doesn't currently set an explicit
  `font-weight` in the inline rule, so *add* `font-weight: 500;` to it rather
  than changing an existing value)

- [ ] **Step 4: Verify**

Run the verification helper for all three themes with `--settings` open
(covers the generic `label` change, which is the highest-blast-radius single
edit in this task since it affects every form label in Settings). Read each
screenshot and confirm every settings toggle/field label is still legible and
just visually lighter — this is the one step in the whole plan most likely to
reveal an unexpected regression, so look carefully, not just glance.

- [ ] **Step 5: Commit**

```bash
git add static/styles.css
git commit -m "style: migrate history-card to design tokens, flatten its hover state, finish eyebrow-label weight pass"
```

---

### Task 9: Phase 3 — full regression pass across all themes + admin.html

**Files:** none (verification only, no code changes expected)

**Interfaces:** none.

- [ ] **Step 1: Screenshot all three themes, main view and settings, one more time**

```bash
python scratchpad_shot.py midnight-slate final-midnight-main.png
python scratchpad_shot.py midnight-slate final-midnight-settings.png --settings
python scratchpad_shot.py daylight final-daylight-main.png
python scratchpad_shot.py daylight final-daylight-settings.png --settings
python scratchpad_shot.py aurora-contrast final-aurora-main.png
python scratchpad_shot.py aurora-contrast final-aurora-settings.png --settings
```

Read all six and confirm: consistent corner radius across cards/buttons/
inputs within each screenshot, consistent label weight, gallery icons neutral
at rest, no overflow/clipping, no theme has lost text/icon contrast.

- [ ] **Step 2: Confirm admin.html still loads correctly**

Find the real admin route (check `app/adapters/inbound/web/routes.py` for the
actual path — it is not `/admin.html` as a static file, it's served by a
FastAPI route, likely `/admin`). Load it in a Playwright screenshot the same
way as above (no theme/localStorage evaluation needed, just
`page.goto(...); page.screenshot(...)`) and confirm no visual break — this
page shares `static/styles.css` and picked up every change in this plan for
free, so this step is verification only, not new work.

- [ ] **Step 3: No commit — this task is verification-only**

If Steps 1-2 reveal a regression, fix it in the relevant earlier task's file
and re-run that task's own verification before returning here, rather than
patching ad hoc at the end.
