#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Export SVG frames of the terminal UI following a stored run (used for the README GIF).

Usage: python scripts/tui_frames.py <run_id> <out_dir> <n1,n2,...>   # frames after n1, n2... events
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.tui.app import SiliconForgeTUI  # noqa: E402


async def main(run_id: str, out: Path, counts: list[int]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    app = SiliconForgeTUI()
    all_events = app.store.events_after(run_id, -1, limit=100_000)
    final_run = app.store.get_run(run_id)
    shown = {"n": 0}

    real_events_after = app.store.events_after
    app.store.events_after = lambda rid, after, limit=500: [e for e in real_events_after(rid, after, limit) if e["seq"] < shown["n"]]

    def fake_get_run(rid: str):
        run = dict(final_run)
        if shown["n"] < len(all_events):
            run.update(status="running", signoff=0, error=None)
        return run

    app.store.get_run = fake_get_run
    async with app.run_test(size=(150, 36)) as pilot:
        app.query_one("#design").value = "up_down_counter"
        app.run_id = run_id
        app.sub_title = f"up_down_counter  ·  {run_id}"
        for i, n in enumerate(counts, 1):
            shown["n"] = n
            await pilot.pause(0.8)
            (out / f"t{i:02d}.svg").write_text(app.export_screenshot(title="SiliconForge"), encoding="utf-8")
    print(f"wrote {len(counts)} frames to {out}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[2]), [int(x) for x in sys.argv[3].split(",")]))
