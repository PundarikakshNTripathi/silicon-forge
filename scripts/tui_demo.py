#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Record the chat TUI for the README: SVG frames from a scripted conversation, rasterised to PNG with a headless browser.

The model is scripted (a fixed list of replies), the tools are real (list, read, edit with approval, lint), and the screenshots
say so. Usage: python scripts/tui_demo.py OUT_DIR [--light]
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.orchestrator.agent.session import AgentSession  # noqa: E402
from services.orchestrator.agent.tools import ToolBox  # noqa: E402
from services.orchestrator.config import InferenceConfig  # noqa: E402
from services.orchestrator.inference.catalog import Catalog, Target  # noqa: E402
from services.orchestrator.inference.cost_tracker import CostTracker  # noqa: E402
from services.orchestrator.inference.providers.base import BaseLLMProvider, LLMResponse  # noqa: E402
from services.orchestrator.inference.router import ModelRouter  # noqa: E402
from services.orchestrator.workspace import init_workspace  # noqa: E402
from services.tui.chat import ApprovalModal, ChatApp  # noqa: E402

COUNTER = """module counter (
  input  logic       clk,
  input  logic       en,
  output logic [7:0] q
);
  always_ff @(posedge clk) begin
    if (en) q <= q + 8'd1;
  end
endmodule
"""
OLD = "  always_ff @(posedge clk) begin\n    if (en) q <= q + 8'd1;\n  end"
NEW = "  always_ff @(posedge clk or negedge rst_n) begin\n    if (!rst_n) q <= 8'd0;\n    else if (en) q <= q + 8'd1;\n  end"
SAY = ("Added an active-low asynchronous reset `rst_n` to `counter`. The counter now clears to 0 on reset. "
       "Lint shows no new warnings.")


def replies() -> list[dict]:
    def act(say, tool=None, **args):
        return {"say": say, "tool": tool, "args": args}

    return [act("Let me look at the project first.", "list_dir", path="."),
            act("Reading the counter.", "read_file", path="rtl/counter.sv"),
            act("The counter has no reset. I will add one and its port.", "edit_file", path="rtl/counter.sv",
                old="  input  logic       clk,", new="  input  logic       clk,\n  input  logic       rst_n,"),
            act("Now the reset behaviour.", "edit_file", path="rtl/counter.sv", old=OLD, new=NEW),
            act("Linting the result.", "lint_rtl", path="rtl/counter.sv"),
            act(SAY)]


class Script(BaseLLMProvider):
    name, local = "script", True

    def __init__(self, items: list[dict], delay: float) -> None:
        self.items, self.delay = list(items), delay

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        time.sleep(self.delay)
        return LLMResponse(content=json.dumps(self.items.pop(0)), prompt_tokens=420, completion_tokens=36, model=model, provider="script")


def make_app(root: Path, light: bool) -> ChatApp:
    ws = init_workspace(root / "counter-demo", name="counter-demo", profile="dev", backend="wsl")
    (ws.root / "rtl").mkdir()
    (ws.root / "rtl" / "counter.sv").write_text(COUNTER, encoding="utf-8")
    catalog = Catalog(providers={"script": {"kind": "replay", "local": True}},
                      profiles={"dev": {t: [Target("script", "m")] for t in ("local_fast", "balanced", "frontier_reasoning")}})

    def factory(on_event, approver):
        router = ModelRouter(InferenceConfig(profile="dev", enable_semantic_cache=False), CostTracker(),
                             provider=Script(replies(), 0.55), catalog=catalog)
        return AgentSession(router, ws, ToolBox(ws), mode="ask", approver=approver, on_event=on_event)

    return ChatApp(ws, session_factory=factory, animate=True, theme="siliconforge-light" if light else "siliconforge-dark")


async def record(out: Path, light: bool) -> int:
    out.mkdir(parents=True, exist_ok=True)
    app = make_app(Path(tempfile.mkdtemp(prefix="sf-tui-demo-")), light)
    n = {"i": 0}

    def shot(pilot_app, tag: str = "") -> None:
        n["i"] += 1
        (out / f"f{n['i']:03d}.svg").write_text(pilot_app.export_screenshot(title="SiliconForge"), encoding="utf-8")

    async with app.run_test(size=(112, 34)) as pilot:
        for _ in range(16):  # the splash draws itself
            await pilot.pause(0.09)
            shot(app)
        await pilot.press("escape")
        await pilot.pause(0.3)
        shot(app)
        app.query_one("#composer").value = "add an active-low reset to the counter and lint it"
        shot(app)
        await pilot.press("enter")
        last = ""
        for _ in range(160):
            await pilot.pause(0.22)
            if isinstance(app.screen, ApprovalModal):
                shot(app)
                shot(app)
                await pilot.press("a")  # approve, and allow this tool for the rest of the session
                await pilot.pause(0.25)
            snap = app.export_screenshot()
            if snap != last:
                shot(app)
                last = snap
            if not app.busy and app.turns:
                break
        await pilot.pause(0.4)
        shot(app)
        shot(app)
        await pilot.press("shift+tab")
        await pilot.pause(0.2)
        shot(app)
    return n["i"]


def rasterise(folder: Path) -> None:
    browser = next((p for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", shutil.which("chromium") or "") if p and Path(p).exists()), None)
    if browser is None:
        print("no Chrome, Edge or Chromium found: SVG frames left as they are", file=sys.stderr)
        return
    for svg in sorted(folder.glob("f*.svg")):
        head = svg.read_text(encoding="utf-8")[:600]
        m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', head)
        w, h = (int(float(m.group(1))) + 1, int(float(m.group(2))) + 1) if m else (1100, 700)
        png = svg.with_suffix(".png")
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={w},{h}", f"--screenshot={png}",
                        svg.resolve().as_uri()], check=False, capture_output=True, timeout=60)


def main() -> int:
    out = Path(sys.argv[1])
    count = asyncio.run(record(out, "--light" in sys.argv))
    rasterise(out)
    print(f"{count} frames in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
