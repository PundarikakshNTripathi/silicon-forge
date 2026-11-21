#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn the PNG frames from `scripts/tui_demo.py` into a looping GIF (no captions: the UI explains itself).

Usage: python scripts/frames_to_gif.py FRAMES_DIR OUT.gif [WIDTH]
The first 16 frames are the splash (fast); a frame identical to the previous one is dropped; the approval and final frames are held.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops


def main() -> int:
    folder, out = Path(sys.argv[1]), Path(sys.argv[2])
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 900
    files = sorted(folder.glob("f*.png"))
    if not files:
        print("no frames", file=sys.stderr)
        return 1
    frames: list[Image.Image] = []
    durations: list[int] = []
    prev: Image.Image | None = None
    for i, f in enumerate(files):
        img = Image.open(f).convert("RGB")
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
        if prev is not None and ImageChops.difference(img, prev).getbbox() is None:
            durations[-1] += 250  # an unchanged frame holds the previous one longer
            continue
        frames.append(img)
        durations.append(80 if i < 16 else 260)
        prev = img
    durations[-1] = 2600  # hold the finished conversation
    # the diff modal is the thing to read: the frame after the splash that has the largest change from its neighbours is held
    pal = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f in frames]
    pal[0].save(out, save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True, disposal=1)
    print(f"{out} {out.stat().st_size / 1024:.0f} KB, {len(frames)} frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
