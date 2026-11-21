# Cockpit design system

The cockpit is an engineering sheet. A verification run is a report you can sign, so the interface is built around three
questions an engineer asks before trusting a result: **what was run, what produced the evidence, and how much margin is there?**

## Idea

- **Title block, not hero metrics.** Every run opens with a ruled title block (design, RTL revision, evidence source, status, time and
  cost), the way a drawing sheet states what it depicts. Results follow as a table against the contract's own limits.
- **Provenance is the loudest thing.** Real tool output is a solid ink stamp (`real · WSL`); surrogate output is a hatched outline
  (`simulated`). The distinction is shape and pattern, not only colour, so it survives colour blindness and greyscale print. The
  hatch also fills timeline pulses and results tables for simulated runs.
- **The flow is a timing diagram.** One row per stage, real elapsed time on the x axis, a pulse where the stage worked, an amber square
  where a stage sent the design back for repair. It is derived from event timestamps, not drawn to look busy.
- **Rules, not cards.** Sections are separated by a heavy top rule and hairlines. No nested cards, no icon-in-circle rows, no
  hero-metric template, no gradients.

## Tokens (`src/styles.css`)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--paper` / `--paper-2` | `#f5f6f8` / `#ffffff` | `#0f151d` / `#141c26` | page and inputs |
| `--sunken` | `#eceef2` | `#0a0f16` | index column, code, tracks |
| `--ink` | `#0e1520` | `#e8edf4` | text, stamps, heavy rules |
| `--ink-2`, `--ink-3` | `#3c4657`, `#626c7b` | `#b2bccb`, `#8593a6` | secondary text (both pass 4.5:1 on paper) |
| `--rule`, `--rule-strong` | `#c9cfd8`, `#8b95a3` | `#243040`, `#4a586b` | hairlines |
| `--amber` | `#f2a900` | `#ffb81c` | selection, focus, the primary action, repair markers |
| `--pass`, `--fail` | `#17775a`, `#b2392b` | `#4cc59a`, `#f27a68` | state only, never decoration |

Amber is photoresist amber, the safelight colour of a cleanroom. It is used for exactly four things: the selected row or tab, focus and
text selection, the primary button, and "this stage was sent back". Nothing else is amber, including simulated data.

## Type

Atkinson Hyperlegible Next for everything (designed for legibility; tabular numerals on), Commit Mono for code, RTL and logs. Fixed scale
(13, 15, 18, 26 px), no fluid type. Self-hosted through `@fontsource`; the app makes no network requests for fonts.

## Components

- Sections: `.panel` (heavy top rule, hairline under its caption). Controls: 2 px radius, 1.5 px ink border; the primary button has a
  hard 2 px offset shadow that collapses when pressed, the only decorative depth in the system.
- Choices (`.choice`): ruled list rows. Selected = amber bar on the left and an ink outline; never a floating card.
- Stamps: `.prov.real`, `.prov.sim`, and `.badge.*` for state.
- Tabs: heavy underline rule, amber thumb under the active tab.
- Everything has hover, focus-visible (2 px ink outline, amber on dark), disabled and active states; `prefers-reduced-motion` removes all motion.

## Layout

An index of sheets on the left (text labels, grouped, the active row marked by the amber bar), the sheet on the right. Below 760 px
the index becomes a bottom bar. Wide tables scroll horizontally instead of clipping.

## Brand mark

A wafer with its flat edge, and a Manhattan-routed **S** whose two ends land on amber via pads (`assets/logo.svg`). One shape reads as
wafer, routed conductor and initial. It uses the page colours in dark mode, so the S never disappears.

## What not to add

A gradient, a glass panel, a card grid, a kicker above a heading, section numbers, an icon in a coloured circle, or any amber that does
not mean one of the four things above.
