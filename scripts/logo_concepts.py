"""Write a logo concept sheet as SVG for review (an SVG sheet). Concepts are plain SVG strings.

The mark has to say "silicon, verified, forged" without drawing a chip with pins: that is the category cliche. Ideas:
  A  Notch: the flat edge every silicon wafer has, cut as a bite out of a circle, with a single amber conductor
     crossing the flat. It is a wafer (round), a fab fiducial (the notch), and a trace (the amber line) in one shape.
  B  Anvil of layers: an anvil silhouette built from stacked metal layers, the top one amber.
  C  Via: a stack of squares of decreasing size seen from above (contact -> via -> metal), amber at the centre.
Only one ships.
"""
from __future__ import annotations

import sys
from pathlib import Path

INK, AMBER, PAPER = "#0e1520", "#f2a900", "#f5f6f8"

# A: wafer with the flat; a Manhattan conductor crosses the flat and ends in a via
A = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<path d="M32 4 A28 28 0 1 1 4 32 A28 28 0 0 1 14.5 10.2 L14.5 21.5 L8.6 21.5 A24 24 0 0 0 8 32 A24 24 0 1 0 32 8 Z" fill="{INK}"/>
<path d="M14.5 10.2 A28 28 0 0 1 32 4 L32 8 A24 24 0 0 0 18.6 13.6 Z" fill="{INK}"/>
<path d="M4 38 H24 V26 H40 V44 H60" fill="none" stroke="{AMBER}" stroke-width="5" stroke-linejoin="miter" stroke-linecap="butt"/>
<rect x="55" y="39" width="9" height="10" fill="{AMBER}"/>
</svg>"""

# A2: cleaner version: a circle with a flat chord cut, conductor stepping across
A2 = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<path d="M32 3 C48 3 61 16 61 32 C61 48 48 61 32 61 C16 61 3 48 3 32 C3 25 5.4 18.800 9.500 13.800 L21 20.500 L21 31 L9.200 31 L9.200 33 L21 33 Z" fill="none"/>
<path fill="{INK}" d="M32 3A29 29 0 1 1 5.500 44.500L5.500 19.500A29 29 0 0 1 32 3Z" opacity="0"/>
<path fill="{INK}" d="M32 3a29 29 0 0 1 0 58A29 29 0 0 1 8 48.500h9A19 19 0 1 0 32 13a19 19 0 0 0-14 6.300V9.500A29 29 0 0 1 32 3Z"/>
<path d="M2 36h19V24h22v20h19" fill="none" stroke="{AMBER}" stroke-width="6" stroke-linejoin="miter"/>
</svg>"""

# B: anvil of three layers
B = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<path d="M6 16 H46 L58 22 L46 28 H36 L36 34 H44 V46 H50 V54 H14 V46 H20 V34 H28 V28 H6 Z" fill="{INK}"/>
<rect x="6" y="16" width="40" height="6" fill="{AMBER}"/>
</svg>"""

# C: via stack
C = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect x="4" y="4" width="56" height="56" fill="{INK}"/>
<rect x="14" y="14" width="36" height="36" fill="{PAPER}"/>
<rect x="22" y="22" width="20" height="20" fill="{INK}"/>
<rect x="28" y="28" width="8" height="8" fill="{AMBER}"/>
</svg>"""

# D: a flat-notched wafer as a negative space "S" path: conductor forms the S inside the circle
D = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<circle cx="32" cy="32" r="29" fill="{INK}"/>
<path d="M45 18 H24 V30 H40 V42 H19" fill="none" stroke="{PAPER}" stroke-width="5" stroke-linejoin="miter"/>
<rect x="43" y="14" width="9" height="9" fill="{AMBER}"/>
<rect x="12" y="38" width="9" height="9" fill="{AMBER}"/>
<path d="M32 3 v6" stroke="{PAPER}" stroke-width="0"/>
</svg>"""

CONCEPTS = {"A notch": A, "A2 notch": A2, "B anvil": B, "C via": C, "D trace-S": D}


def main(out: str) -> None:
    cells = []
    for i, (name, svg) in enumerate(CONCEPTS.items()):
        for j, bg in enumerate((PAPER, "#0f151d")):
            cells.append((i, j, name, svg, bg))
    w, h, pad = 220, 240, 20
    body = ""
    for i, j, name, svg, bg in cells:
        x, y = pad + i * (w + pad), pad + j * (h + pad)
        inner = svg.replace("<svg ", f'<svg x="{x + 30}" y="{y + 20}" width="160" height="160" ').replace('xmlns="http://www.w3.org/2000/svg"', "")
        if j == 1:
            inner = inner.replace(INK, "#e8edf4")
        body += f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{bg}" stroke="#8b95a3"/>{inner}<text x="{x + 12}" y="{y + h - 14}" font-family="sans-serif" font-size="14" fill="#8b95a3">{name}</text>'
    sheet = f'<svg xmlns="http://www.w3.org/2000/svg" width="{pad + len(CONCEPTS) * (w + pad)}" height="{pad + 2 * (h + pad)}"><rect width="100%" height="100%" fill="#dfe3e8"/>{body}</svg>'
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(sheet, encoding="utf-8")  # render it in a browser (cairosvg needs native Cairo on Windows)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "assets/brand/concepts.svg")
