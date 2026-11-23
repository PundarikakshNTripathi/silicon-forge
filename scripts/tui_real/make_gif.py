# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import subprocess
import sys
from pathlib import Path

out = Path(sys.argv[1])
ff = sys.argv[2]
dest = Path(sys.argv[3])
cap = float(sys.argv[4]) if len(sys.argv) > 4 else 1.4  # a pause longer than this is shortened to it
frames = json.loads((out / "frames.json").read_text())
lines = []
total = 0.0
for i, f in enumerate(frames):
    gap = (frames[i + 1]["t"] - f["t"]) / 1000 if i + 1 < len(frames) else 2.0
    d = min(max(gap, 0.04), cap)
    total += d
    lines.append(f"file '{(out / f['file']).as_posix()}'")
    lines.append(f"duration {d:.3f}")
lines.append(f"file '{(out / frames[-1]['file']).as_posix()}'")
(out / "list.txt").write_text("\n".join(lines), encoding="utf-8")
vf = ("fps=8,scale=800:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96:stats_mode=diff[p];"
      "[s1][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(out / "list.txt"), "-vf", vf, "-loop", "0", str(dest)], check=True)
print(f"{len(frames)} frames, {total:.1f} s after shortening pauses to {cap} s, {dest.stat().st_size / 1e6:.2f} MB")
