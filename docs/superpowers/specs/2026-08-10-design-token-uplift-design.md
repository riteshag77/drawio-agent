# Design token uplift — sharp/modern visual system

## Context

The app ships three color themes (`midnight-slate`, `daylight`, `aurora-contrast`) as
separate files under `static/themes/`, each overriding a shared set of CSS custom
properties consumed by `static/styles.css`. Colors are already theme-owned and
correctly isolated. Structure is not: `styles.css` hardcodes its own spacing,
corner radius, and shadow values inline, ad hoc, per selector. A single unused
`--radius: 12px` token exists (used once, at `styles.css:3561`); everything else
hand-picks its own radius (8px, 10px, 11px, 14px, 999px all appear, inconsistently,
for visually similar surfaces), and there are 8 distinct one-off `box-shadow`
values instead of a shared set. `admin.html` also loads `static/styles.css`, so it
inherits whatever the base file does.

This was surfaced while iterating on `aurora-contrast.css`'s "Photon Sphere" look:
successive passes fixed color (v2) and then added theme-local overrides for
radius/spacing/letter-spacing/icon-color to make that one theme feel sharper (v3).
The user then asked for that sharpness "all over the application," which properly
belongs in the shared structural layer, not duplicated per theme.

## Goal

Make corner radius, spacing, type treatment, icon-color behavior, and elevation
consistent across the whole app (all three themes + admin.html), matching a
Linear/Raycast-style precision-tool feel: tight spacing grid, small consistent
radii, minimal/no decorative shadows, restrained single-accent color used only for
interactive/active state, monospace for numeric/technical readouts.

**Explicitly not a goal:** no HTML markup changes, no JS changes, no new
components, no behavior changes, no change to what each theme's colors are.
`daylight` stays a light theme, `midnight-slate` and `aurora-contrast` stay dark —
only their shared shape/spacing/type/icon-behavior language becomes consistent.

## Design

### 1. New token layer (`static/styles.css`, `:root`, additive)

```css
:root {
  /* spacing (4px base grid) */
  --space-1: 0.25rem;  --space-2: 0.5rem;  --space-3: 0.75rem;
  --space-4: 1rem;     --space-5: 1.25rem; --space-6: 1.5rem;  --space-8: 2rem;

  /* radius */
  --radius-sm: 6px;     /* inputs, chips, badges, small buttons */
  --radius-md: 8px;     /* cards, buttons, most surfaces */
  --radius-lg: 12px;    /* panels, modals, composer */
  --radius-pill: 999px; /* status pills — unchanged */

  /* type */
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: ui-monospace, 'SFMono-Regular', 'JetBrains Mono', Menlo, monospace;

  /* elevation */
  --shadow-focus: 0 0 0 3px var(--accent-glow);
  --shadow-panel: 0 24px 60px rgba(0,0,0,0.45); /* modals/drawers — already used 3x verbatim */
  --transition-fast: 120ms ease;   /* hover/color/background */
  --transition-panel: 180ms ease;  /* width/collapse — existing timing, unchanged */
}
```

The existing `--radius: 12px` token is removed in favor of the `sm/md/lg/pill`
scale (its one caller at `styles.css:3561` moves to `--radius-lg`).

### 2. Two global behavior rules

- **Icon color**: icons default to `var(--text-secondary)`; they brighten to
  `var(--accent)` only on hover, active, or selected state. Today several
  selectors hardcode `color: var(--accent)` as an icon's resting state (e.g.
  `.gallery-card-icon`); these change to resting-neutral +
  hover/active-accent, consistently, everywhere. This turns color into a
  meaningful signal ("this is interactive/selected") instead of decoration,
  in every theme, not just `aurora-contrast`.
- **Numeric/technical values use `var(--font-mono)`**: cost amounts, token
  meter counts, log timestamps, version/build tags. Purely a `font-family`
  addition on existing selectors — no markup change.

### 3. Migration, staged (each stage ends with a 3-theme screenshot check)

- **Phase 1 — highest-traffic surfaces**: sidebar, composer, gallery cards,
  chat area, settings drawer, buttons/inputs, segmented control. Replace
  their hardcoded `border-radius`/`padding`/`gap`/`box-shadow` literals with
  the token set above.
- **Phase 2 — secondary panels**: cost breakdown, compliance, governance,
  insights tabs, history/templates, provider picker. Same token migration;
  this is where most of the mono-number wins live (cost figures, token
  meters, timestamps).
- **Phase 3 — admin.html visual pass**: no admin-specific CSS changes are
  expected (it shares `styles.css`), so this phase is verification only —
  confirm nothing regressed once Phases 1–2 land.
- **`aurora-contrast.css` cleanup**: remove the v3 theme-local overrides for
  radius, spacing, and eyebrow-label letter-spacing (redundant once the base
  tokens deliver the same thing globally), and remove the serif
  `font-family`/`font-style: italic` rules added on `.brand-text h1` /
  `.gallery-header p` — dropped in favor of staying consistent with the
  Inter + mono system used everywhere else (a deliberate call: the serif
  experiment fit an editorial mood but not the Linear/Raycast precision-tool
  direction that was chosen for the global system). Keep the genuinely
  color-specific pieces: icon hover-to-accent behavior (now the global
  rule's specific accent color), flattened (non-blurred) card backgrounds,
  and border colors.

### 4. Verification

- After each phase, screenshot `midnight-slate`, `daylight`, and
  `aurora-contrast` (Playwright, already set up in this session) side by side
  and confirm: no layout shift/overflow, radius/spacing reads consistent,
  focus rings still visible, no theme lost contrast on text/icons.
- Re-run the dataviz `contrast()` check on any color pairing touched
  incidentally (should be none — this spec is structure-only).

## Risks

- `styles.css` is ~3800 lines with many one-off values; a blind global
  find-replace could catch a value that a specific theme quietly depends on
  (e.g. a shadow giving a light-theme modal enough contrast). Mitigated by
  staging + per-phase screenshot checks rather than one bulk pass.
- Icon-color-rule flip (accent → neutral resting state) touches more
  selectors than just `.gallery-card-icon`; each one needs its hover/active
  state added at the same time it loses its resting accent color, or icons
  will look permanently muted with no interactive feedback.
