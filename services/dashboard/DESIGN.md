# Cockpit design system: the layer stack

A verification run is a report you can sign, so the interface answers three questions before anything else: **what was run, what
produced the evidence, and how much margin is there?** The look comes from the thing being built: chip layout viewers give every layer
its own hue so a stack of silicon reads at a glance, and the cockpit borrows that.

## Idea

- **One hue per stage, and it never means anything else.** Contract violet, RTL vermilion, guardrails ochre, formal cobalt, synthesis
  green, place and route magenta, signoff navy. The flow strip is a layer cross-section; the same hue marks that stage's panels, its
  log lines, the sidebar's stack bar, the TUI and the plots (`configs/design/tokens.json` is the single source).
- **Amber means "sent back for repair", and nothing else.** A diamond on the stage that sent the design back. Selection, focus and the
  primary action are cobalt; success is green; failure is red.
- **Provenance is a shape, not a colour.** Real tool output is a solid stamp (`real · WSL`) and solid bars; surrogate output is a hatched
  outline and hatched bars in the stage's hue. It survives colour blindness and greyscale print.
- **Title block, not hero metrics.** Every run opens with what was run, then results against the contract's own limits, then the flow.
- **Rules, not cards.** Sections are separated by a top rule (the stage hue when the section belongs to a stage) and hairlines.

## Tokens (`configs/design/tokens.json` → `src/tokens.css`, `services/tui/tokens.py`)

`python scripts/gen_tokens.py` regenerates both; `tests/test_design_tokens.py` fails if they are stale, if any text pair drops below
4.5:1 in either theme, or if a stage hue falls below 3:1 against the page or collides with amber.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--chrome` | `#0b1b33` | `#060e1c` | sidebar, toasts |
| `--paper`, `--paper-2`, `--sunken` | `#f7f5f0`, `#fffefb`, `#efece4` | `#0a1424`, `#101c30`, `#070f1d` | page, inputs, code and tracks |
| `--ink`, `--ink-2`, `--ink-3` | `#0b1b33`, `#37435c`, `#5b6680` | `#e6ecf7`, `#b3c0d6`, `#8496b3` | text |
| `--accent` | `#1f55c9` | `#6aa7ff` | links, selection, focus, primary action |
| `--layer-*` | see JSON | see JSON | stage identity (graphics, rules, swatches; text stays ink) |
| `--repair` | `#f2a900` | `#ffb81c` | "sent back for repair" only |
| `--pass`, `--fail` | `#0d7556`, `#ae2d19` | `#47cf9f`, `#ff8a78` | state, never decoration |

Guardrails ochre is `#b07d00` (not a brighter yellow) so it stays apart from amber and keeps 3:1 on paper.

## Type

Atkinson Hyperlegible Next for everything, Commit Mono for code, RTL and logs. Fixed scale (13, 15, 18, 26 px). Self-hosted through
`@fontsource`; the app makes no network requests for fonts.

## Components

- Sidebar: deep navy, a seven-segment stack bar under the wordmark, the active row a lighter navy with a bar on the edge. Below 760 px it
  becomes a bottom bar.
- Sections: `.panel` with a heavy top rule, hairline under its caption; `data-layer` gives it the stage hue.
- Controls: cobalt primary button with a soft lift; secondary buttons outlined; every control has hover, focus-visible (2 px cobalt), disabled and active states.
- Choices (`.choice`): ruled list rows, selected by a cobalt outline on a cobalt wash.
- Stamps: `.prov.real`, `.prov.sim`, and `.badge.*` for state.
- Motion only on state change (a running run pulses, hover and press feedback); `prefers-reduced-motion` removes all of it.

## Brand mark

One conductor routed through five metal layers, drawn as an S: top bar contract, then RTL, formal, synthesis and place and route, each in
its own layer hue. It starts at an open pad (the spec) and ends on a solid one (signoff). `assets/logo.svg`, `src/Mark.tsx` and
`public/logo.svg` (the favicon) are the same drawing; it follows the page theme.

## What not to add

A gradient, a glass panel, a card grid, a kicker above a heading, section numbers, an icon in a coloured circle, a coloured left border
on list items, or any amber that does not mean "sent back for repair".
