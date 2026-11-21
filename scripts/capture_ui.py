#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capture the README's web screenshots from a running cockpit with headless Chrome (or Edge).

Usage: python scripts/capture_ui.py --base http://localhost:5173 --run RUN_ID --out assets/screenshots

The app picks its view from the URL (`?tab=`, `?theme=`), so no clicking is needed and every shot is reproducible. The run should be a
real-tool run (the page shows its `real` stamp); the screenshots show whatever the API serves, nothing is mocked.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]


def shots(run: str) -> list[tuple[str, str, int, int]]:
    """(file name, path and query, width, height)"""
    return [
        ("run-overview", f"/runs/{run}?theme=light", 1440, 1060),
        ("run-overview-dark", f"/runs/{run}?theme=dark", 1440, 1060),
        ("verification", f"/runs/{run}?tab=verify&theme=light", 1440, 1480),
        ("performance", f"/runs/{run}?tab=ppa&theme=light", 1440, 1750),
        ("layout", f"/runs/{run}?tab=layout&theme=light", 1440, 1500),
        ("agent-loops", "/evals?tab=agents&theme=light", 1440, 1560),
        ("insights", "/cost?theme=light", 1440, 1000),
        ("new-run", "/?theme=light", 1440, 900),
        ("run-overview-phone", f"/runs/{run}?theme=light", 390, 1500),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5173")
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", default="assets/screenshots")
    ap.add_argument("--only", default="", help="comma-separated shot names")
    a = ap.parse_args()
    browser = next((b for b in BROWSERS if Path(b).exists()), shutil.which("chromium") or shutil.which("google-chrome"))
    if not browser:
        print("no Chrome, Edge or Chromium found", file=sys.stderr)
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    wanted = {s for s in a.only.split(",") if s}
    for name, path, w, h in shots(a.run):
        if wanted and name not in wanted:
            continue
        target = out / f"{name}.png"
        sep = "&" if "?" in path else "?"
        url = f"{a.base.rstrip('/')}{path}{sep}shot=1"
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        f"--window-size={w},{h}", "--virtual-time-budget=9000", f"--screenshot={target}", url],
                       check=False, capture_output=True, timeout=120)
        print(f"{target} {'ok' if target.exists() else 'MISSING'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
