# SPDX-License-Identifier: AGPL-3.0-or-later
"""SiliconForge terminal UI (Textual). Runs the pipeline in-process and follows its event stream live."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Button, DataTable, Footer, Header, Label, Select, Static

from services.orchestrator.api.manager import RunManager, RunRequest
from services.orchestrator.api.store import TERMINAL, Store
from services.orchestrator.paths import RESOURCE_ROOT

REPO = RESOURCE_ROOT
STAGES = [("ARCHITECT", "Contract"), ("RTL", "RTL"), ("GUARDRAIL_AUDIT", "Guardrails"), ("FORMAL_VERIFY", "Formal"),
          ("SYNTHESIS_LEC", "Synthesis"), ("PNR_RL_OPT", "Layout"), ("SIGNOFF", "Signoff")]
ALIAS = {"RTL_GEN": "RTL", "SIMULATION": "GUARDRAIL_AUDIT"}
PROFILES = ["replay", "local", "hybrid", "cloud", "openai", "gemini", "subscription"]
BACKENDS = ["simulated", "wsl", "native", "docker"]


def stage_states(events: list[dict[str, Any]]) -> dict[str, str]:
    """Event stream -> {stage: pending|active|done|fail}."""
    st = {k: "pending" for k, _ in STAGES}
    last: str | None = None
    for e in events:
        key = ALIAS.get(e["stage"], e["stage"])
        if key in ("FAILED", "CANCELLED") and last:
            st[last] = "fail"
            continue
        if key not in st:
            continue
        last = key
        if e["status"] in ("PASS", "INFO") and key not in ("RTL",):
            st[key] = "done"
        elif e["status"] == "FAIL" or (key == "SIGNOFF" and e["status"] == "WARN"):
            st[key] = "fail"
        else:
            st[key] = "active"
    if st["RTL"] == "active" and st["GUARDRAIL_AUDIT"] != "pending":
        st["RTL"] = "done"
    return st


def render_strip(states: dict[str, str], simulated: bool) -> str:
    glyph = {"pending": "○", "active": "◉", "done": "░" if simulated else "●", "fail": "✘"}
    color = {"pending": "dim", "active": "bold dodger_blue1", "done": "dark_orange" if simulated else "green", "fail": "red"}
    parts = []
    for key, label in STAGES:
        s = states[key]
        text = f"[bold]{label}[/]" if s == "active" else label
        parts.append(f"[{color[s]}]{glyph[s]}[/] {text}")
    return "  [dim]━━[/]  ".join(parts)


class StripPanel(Static):
    pass


class SiliconForgeTUI(App):
    CSS = """
    Screen { background: $surface; }
    #side { width: 38; padding: 1 2; border-right: tall $primary-darken-2; }
    #main { padding: 1 2; }
    #strip { height: 3; content-align: left middle; }
    #banner { height: 1; margin-bottom: 1; }
    #stats { height: 3; margin: 1 0; }
    DataTable { height: 1fr; }
    Label.h { text-style: bold; margin-top: 1; }
    """
    BINDINGS = [("s", "start", "Start run"), ("c", "cancel", "Cancel"), ("q", "quit", "Quit")]
    TITLE = "SiliconForge"
    SUB_TITLE = "autonomous spec to GDSII"
    run_id: reactive[str | None] = reactive(None)

    def __init__(self, db_path: Path | None = None, runs_root: Path | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.store = Store(db_path or REPO / "runs" / "siliconforge.db")
        self.manager = RunManager(self.store, REPO, runs_root=runs_root)
        self.examples = {p.parent.parent.name: (p.parent.parent / "spec.md").read_text(encoding="utf-8")
                         for p in sorted((REPO / "hardware" / "examples").glob("*/fixtures/contract.json"))}
        self.last_seq = -1
        self.events: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="side"):
                yield Label("Design", classes="h")
                yield Select([(n, n) for n in self.examples], value=next(iter(self.examples)), allow_blank=False, id="design")
                yield Label("Model", classes="h")
                yield Select([(p, p) for p in PROFILES], value="replay", allow_blank=False, id="profile")
                yield Label("Where it runs", classes="h")
                yield Select([(b, b) for b in BACKENDS], value="simulated", allow_blank=False, id="backend")
                yield Label("")
                yield Button("Start run", id="start", variant="primary")
                yield Button("Cancel", id="cancel", variant="default")
            with Vertical(id="main"):
                yield Static("", id="banner")
                yield StripPanel("", id="strip")
                yield Static("", id="stats")
                yield DataTable(id="log", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#log", DataTable)
        table.add_columns("#", "Step", "Status", "What happened")
        self.set_interval(0.35, self.poll)
        self.query_one("#strip", StripPanel).update(render_strip(stage_states([]), True))

    def action_start(self) -> None:
        self.start_run()

    @on(Button.Pressed, "#start")
    def start_run(self) -> None:
        design = str(self.query_one("#design", Select).value)
        profile = str(self.query_one("#profile", Select).value)
        backend = str(self.query_one("#backend", Select).value)
        req = RunRequest(spec_text=self.examples[design], profile=profile, backend=backend, inject_bug=profile == "replay")
        self.run_id = self.manager.start(req)
        self.events, self.last_seq = [], -1
        self.query_one("#log", DataTable).clear()
        self.sub_title = f"{design}  ·  {self.run_id}"

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        if self.run_id:
            self.manager.cancel(self.run_id)

    def poll(self) -> None:
        if not self.run_id:
            return
        table = self.query_one("#log", DataTable)
        new = self.store.events_after(self.run_id, self.last_seq)
        for e in new:
            self.last_seq = e["seq"]
            self.events.append(e)
            if e["stage"] != "LLM":
                table.add_row(str(e["seq"]), e["stage"].lower().replace("_", " "), e["status"].lower(), e["detail"][:110])
                table.scroll_end(animate=False)
        run = self.store.get_run(self.run_id)
        if not run:
            return
        simulated = run["backend"] == "simulated"
        self.query_one("#strip", StripPanel).update(render_strip(stage_states(self.events), simulated))
        self.query_one("#banner", Static).update(
            "[black on dark_orange] SIMULATED [/] results come from surrogate models" if simulated
            else f"[black on green] REAL · {run['backend'].upper()} [/] results from real EDA tools")
        calls = [e["payload"] for e in self.events if e["stage"] == "LLM"]
        cost = sum(c.get("cost_usd", 0.0) for c in calls)
        toks = sum(c.get("prompt_tokens", 0) + c.get("completion_tokens", 0) for c in calls)
        verdict = {"succeeded": "[green]passed[/]", "failed": "[red]failed[/]", "running": "[dodger_blue1]running[/]",
                   "queued": "queued", "cancelled": "cancelled", "interrupted": "interrupted"}[run["status"]]
        sign = " · signoff closed" if run["signoff"] else ""
        self.query_one("#stats", Static).update(
            f"Status {verdict}{sign}    Model calls {len(calls)}    Tokens {toks}    Cost ${cost:.4f}"
            + (f"    [red]{run['error']}[/]" if run["error"] else ""))
        if run["status"] in TERMINAL:
            self.run_id_done = run["id"]


def main() -> None:
    SiliconForgeTUI().run()


if __name__ == "__main__":
    main()
    
