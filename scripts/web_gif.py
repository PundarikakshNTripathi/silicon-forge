#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Assemble the frames from scripts/web_demo.cjs into a captioned, looping GIF.

Usage: python scripts/web_gif.py FRAMES_DIR OUT.gif [WIDTH]     (FRAMES_DIR holds f*.png and frames.json: file, ms, caption)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

FONTS = ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def font(size: int) -> ImageFont.ImageFont:
    for f in FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def main() -> int:
    folder, out = Path(sys.argv[1]), Path(sys.argv[2])
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 960
    spec = json.loads((folder / "frames.json").read_text(encoding="utf-8"))
    frames: list[Image.Image] = []
    holds: list[int] = []
    f = font(max(13, width // 62))
    for item in spec:
        img = Image.open(folder / item["file"]).convert("RGB")
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
        d = ImageDraw.Draw(img, "RGBA")
        label = item["caption"]
        w = d.textlength(label, font=f)
        h = f.size + 14
        x0, y0 = 14, img.height - h - 14
        d.rounded_rectangle((x0, y0, x0 + w + 24, y0 + h), radius=8, fill=(20, 26, 40, 225))
        d.text((x0 + 12, y0 + 6), label, font=f, fill=(240, 243, 250, 255))
        if frames and ImageChops.difference(img, frames[-1]).getbbox() is None:
            holds[-1] += item["ms"]
            continue
        frames.append(img)
        holds.append(item["ms"])
    pal = [fr.quantize(colors=160, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for fr in frames]
    pal[0].save(out, save_all=True, append_images=pal[1:], duration=holds, loop=0, optimize=True, disposal=1)
    print(f"{out} {out.stat().st_size / 1024:.0f} KB, {len(frames)} frames, {sum(holds) / 1000:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
