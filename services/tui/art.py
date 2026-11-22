# SPDX-License-Identifier: AGPL-3.0-or-later
"""Terminal art: the animated splash (the logo's conductor routed through five layers) and the stage strip.

Pure functions from (progress or state, tick) to Rich `Text`, so they can be unit-tested and rendered to frames for the README GIF
without a terminal.
"""
from __future__ import annotations

from rich.text import Text

from services.tui.theme import palette

# The mark, in drawing order: five metal layers stacked into an S (same drawing as assets/logo.svg), one row each with a blank row
# between. Each cell is (x, y, glyph, layer). Bars draw left to right, top to bottom, like a layer being deposited.
W, H = 26, 9
BARS = [(0, 0, 26, "contract"), (0, 2, 11, "rtl"), (0, 4, 26, "formal"), (15, 6, 11, "lec"), (0, 8, 26, "pnr")]


def _route() -> list[tuple[int, int, str, str]]:
    return [(x0 + i, y, "█", layer) for x0, y, n, layer in BARS for i in range(n)]


ROUTE = _route()
TAGLINE = "spec  ->  verified RTL  ->  GDSII"


def splash(progress: float, mode: str = "dark", *, width: int = 60) -> Text:
    """One frame of the splash. progress 0..1: the conductor draws itself cell by cell, then the name and tagline appear."""
    pal = palette(mode)
    n = len(ROUTE)
    drawn = min(n, int(progress * 1.15 * n))  # the mark finishes a little before the text
    grid = [[(" ", "")] * W for _ in range(H)]
    for i, (x, y, ch, layer) in enumerate(ROUTE[:drawn]):
        style = f"bold {pal['chrome-ink'] if i == drawn - 1 and drawn < n else pal['layer-' + layer]}"
        grid[y][x] = (ch, style)
    pad = " " * max(0, (width - W) // 2)
    out = Text()
    for row in grid:
        out.append(pad)
        for ch, style in row:
            out.append(ch, style=style or None)
        out.append("\n")
    out.append("\n")
    name = "SiliconForge"
    shown = max(0, min(len(name), int((progress - 0.55) / 0.3 * len(name))))
    out.append(" " * ((width - len(name)) // 2))
    out.append(name[:shown], style=f"bold {pal['ink']}")
    out.append("\n")
    if progress > 0.88:
        out.append(" " * ((width - len(TAGLINE)) // 2) + TAGLINE, style=pal["ink-3"])
    out.append("\n")
    # the legend: seven layers in flow order, drawn with the same hues as the web UI's stack bar
    stack = Text()
    for key in ("contract", "rtl", "guardrails", "formal", "lec", "pnr", "signoff"):
        stack.append("▬▬", style=pal["layer-" + key])
        stack.append(" ")
    if progress > 0.95:
        out.append("\n" + " " * ((width - len(stack.plain)) // 2))
        out.append_text(stack)
    return out


STAGE_KEYS = [("ARCHITECT", "contract", "Contract"), ("RTL", "rtl", "RTL"), ("GUARDRAIL_AUDIT", "guardrails", "Guardrails"),
              ("FORMAL_VERIFY", "formal", "Formal"), ("SYNTHESIS_LEC", "lec", "Synthesis"), ("PNR_RL_OPT", "pnr", "Layout"),
              ("SIGNOFF", "signoff", "Signoff")]


def stage_strip(states: dict[str, str], retries: set[str], simulated: bool, tick: int = 0, mode: str = "dark") -> Text:
    """The flow as a row of layer-coloured stages: solid = real tools, hatched = surrogate, the active stage pulses, and an amber
    diamond follows a stage that sent the design back for repair (amber means nothing else)."""
    pal = palette(mode)
    out = Text()
    for i, (event_stage, layer, label) in enumerate(STAGE_KEYS):
        st = states.get(event_stage, "pending")
        hue = pal["layer-" + layer]
        if st == "done":
            glyph, style = ("▒" if simulated else "█"), hue
        elif st == "active":
            glyph, style = ("█" if tick % 2 == 0 else "▓"), f"bold {hue}"
        elif st == "fail":
            glyph, style = "✘", pal["fail"]
        else:
            glyph, style = "░", pal["rule-strong"]
        out.append(glyph * 2, style=style)
        out.append(" " + label, style=f"bold {pal['ink']}" if st == "active" else (pal["ink-2"] if st == "done" else pal["ink-3"]))
        if event_stage in retries:
            out.append(" ◆", style=f"bold {pal['repair']}")
        if i < len(STAGE_KEYS) - 1:
            out.append("  ")
    return out
