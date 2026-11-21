#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Assemble captioned PNG frames into a looping GIF.

Usage: python scripts/make_gif.py OUT.gif WIDTH FRAMES_DIR  "caption 1" "caption 2" ...
Frames are f01.png, f02.png, ... in FRAMES_DIR; one caption per frame; a frame whose caption starts with '!' is held longer.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def font(size: int) -> ImageFont.ImageFont:
    for f in FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def main() -> int:
    out, width, frames_dir, captions = Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:]
    files = sorted(frames_dir.glob("f*.png"))
    if len(files) != len(captions):
        print(f"{len(files)} frames but {len(captions)} captions")
        return 1
    frames, durations = [], []
    for f, cap in zip(files, captions, strict=True):
        hold = cap.startswith("!")
        cap = cap.lstrip("!")
        im = Image.open(f).convert("RGB")
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        bar = 44
        canvas = Image.new("RGB", (im.width, im.height + bar), (20, 32, 46))
        canvas.paste(im, (0, 0))
        d = ImageDraw.Draw(canvas)
        d.text((18, im.height + 9), cap, font=font(22), fill=(237, 240, 242))
        frames.append(canvas.quantize(colors=160, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
        durations.append(2600 if hold else 1500)
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True, disposal=2)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB, {len(frames)} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
