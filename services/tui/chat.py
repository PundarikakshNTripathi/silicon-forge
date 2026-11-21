# SPDX-License-Identifier: AGPL-3.0-or-later
"""The chat-first terminal UI: a conversation with the workspace agent, tool calls as live cards, edits shown as diffs and approved
in a modal, slash commands, Shift+Tab to cycle permission modes, Esc to interrupt.

The agent session is synchronous and runs in a worker thread; its events and approval requests cross to the UI thread through
`call_from_thread`. Tests inject a scripted session through `session_factory`, so nothing here needs a live model.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.suggester import SuggestFromList
from textual.widgets import DirectoryTree, Footer, Input, Markdown, Static

from services.orchestrator.agent.session import MODES, AgentEvent, AgentSession
from services.tui import art, diffview
from services.tui.theme import DEFAULT, build_themes, palette

SLASH = ["/help", "/mode", "/undo", "/sessions", "/resume", "/memory", "/cost", "/clear", "/theme", "/exit"]
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
MODE_BLURB = {"ask": "confirms edits and commands", "accept-edits": "edits go through, commands are confirmed",
              "auto": "no confirmations", "plan": "read-only: describes changes instead of making them"}
HELP = """**Keys**  `Shift+Tab` cycle mode · `Esc` interrupt · `Ctrl+B` files · `Ctrl+L` clear · `F1` this help · `Ctrl+Q` quit
**Slash commands**  `/mode [ask|accept-edits|auto|plan]` · `/undo` · `/sessions` · `/resume [id]` · `/memory` · `/cost` · `/clear` · `/theme` · `/exit`
Click a tool card to expand its output. Approvals: `y` allow once, `a` always allow this tool in this session, `n` decline."""

SessionFactory = Callable[[Callable[[AgentEvent], None], Callable[[str, str, dict], bool]], AgentSession]


def mode_style(mode: str, pal: dict[str, str]) -> str:
    colour = {"ask": pal["accent"], "accept-edits": pal["pass"], "auto": pal["fail"], "plan": pal["ink-3"]}[mode]
    return f"bold {pal['on-accent'] if mode != 'plan' else pal['paper']} on {colour}"


# ---------------------------------------------------------------------------------------------------------------- widgets

class Splash(Screen):
    """The conductor draws itself through the five layers, then the name. Any key skips it."""

    BINDINGS = [Binding("escape,enter,space", "skip", show=False)]
    FRAMES = 36

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode_name, self.frame = mode, 0

    def compose(self) -> ComposeResult:
        yield Static("", id="splash")

    def on_mount(self) -> None:
        self.set_interval(1 / 24, self._tick)

    def _tick(self) -> None:
        self.frame += 1
        self.query_one("#splash", Static).update(art.splash(min(1.0, self.frame / self.FRAMES), self.mode_name, width=self.size.width))
        if self.frame > self.FRAMES + 14:
            self.dismiss()

    def on_key(self) -> None:
        self.dismiss()

    def action_skip(self) -> None:
        self.dismiss()


class ApprovalModal(ModalScreen[str]):
    """Allow once / always allow this tool / decline, with the change shown as a diff when there is one."""

    BINDINGS = [Binding("y,enter", "answer('yes')", "Allow"), Binding("a", "answer('always')", "Always allow"),
                Binding("n,escape", "answer('no')", "Decline")]

    def __init__(self, kind: str, summary: str, details: dict, diff: list[str], mode: str) -> None:
        super().__init__()
        self.kind, self.summary, self.details, self.diff, self.mode_name = kind, summary, details, diff, mode

    def compose(self) -> ComposeResult:
        pal = palette(self.mode_name)
        tool = self.details.get("tool", "")
        with Vertical(id="approval"):
            yield Static(Text.assemble((f" {self.kind} ", f"bold {pal['on-accent']} on {pal['accent']}"), ("  ", ""), (tool, f"bold {pal['ink']}")), id="a-head")
            body = Text()
            if self.diff:
                add, rem = diffview.counts(self.diff)
                body.append(f"+{add} ", style=f"bold {pal['pass']}")
                body.append(f"-{rem}\n", style=f"bold {pal['fail']}")
                for ln in self.diff:
                    sty = pal["pass"] if ln.startswith("+") and not ln.startswith("+++") else pal["fail"] if ln.startswith("-") and not ln.startswith("---") \
                        else pal["accent"] if ln.startswith("@@") else pal["ink-3"]
                    body.append(ln + "\n", style=sty)
            else:
                body.append(json.dumps(self.details.get("args", {}), indent=2, ensure_ascii=False)[:1500], style=pal["ink-2"])
            with VerticalScroll(id="a-body"):
                yield Static(body)
            yield Static(Text.assemble(("y", f"bold {pal['pass']}"), " allow   ", ("a", f"bold {pal['accent']}"), " always allow this tool   ",
                                       ("n", f"bold {pal['fail']}"), " decline"), id="a-keys")

    def action_answer(self, answer: str) -> None:
        self.dismiss(answer)


class ToolCard(Static):
    """One tool call. Spinner while it runs, then a tick or a cross with the duration; click to expand the output."""

    def __init__(self, name: str, args: dict, permission: str, diff: list[str], mode: str) -> None:
        super().__init__()
        self.tool, self.args, self.permission, self.diff, self.mode_name = name, args, permission, diff, mode
        self.state, self.result, self.started, self.duration = "running", "", time.monotonic(), 0.0
        self.expanded = False
        self.add_class("tool", f"perm-{permission}", "st-running")

    def summary(self) -> str:
        a = self.args
        if "path" in a:
            return str(a["path"])
        if self.tool == "eda":
            return " ".join([str(a.get("tool", "")), *map(str, a.get("args", []))])[:80]
        return json.dumps(a, ensure_ascii=False)[:80] if a else ""

    def finish(self, state: str, result: str) -> None:
        self.state, self.result, self.duration = state, result, time.monotonic() - self.started
        self.remove_class("st-running")
        self.add_class(f"st-{state}")
        self.refresh_card()

    def tick(self, n: int) -> None:
        if self.state == "running":
            self.refresh_card(n)

    def refresh_card(self, n: int = 0) -> None:
        pal = palette(self.mode_name)
        icon, colour = {"running": (SPINNER[n % len(SPINNER)], pal["accent"]), "ok": ("✓", pal["pass"]),
                        "error": ("✗", pal["fail"]), "denied": ("⊘", pal["ink-3"])}[self.state]
        dur = f"{(time.monotonic() - self.started) if self.state == 'running' else self.duration:.1f}s"
        head = Text.assemble((icon + " ", f"bold {colour}"), (self.tool, f"bold {pal['ink']}"), ("  " + self.summary(), pal["ink-2"]),
                             ("  " + dur, pal["ink-3"]))
        body = Text()
        if self.diff:
            add, rem = diffview.counts(self.diff)
            body.append(f"\n  +{add} ", style=f"bold {pal['pass']}")
            body.append(f"-{rem}", style=f"bold {pal['fail']}")
            for ln in (self.diff[3:63] if self.expanded else diffview.compact(self.diff)):
                sty = pal["pass"] if ln.startswith("+") else pal["fail"] if ln.startswith("-") else pal["ink-3"]
                body.append("\n  " + ln, style=sty)
        if self.result and self.state != "running" and not (self.diff and self.state == "ok" and self.result.startswith("{")):
            lines = diffview.pretty(self.tool, self.result) if self.state == "ok" else self.result.splitlines()
            keep = 40 if self.expanded else 5
            for ln in lines[:keep]:
                body.append("\n  " + ln[:160], style=pal["ink-3"] if self.state == "ok" else pal["fail"])
            if len(lines) > keep:
                body.append(f"\n  ... {len(lines) - keep} more lines (click to expand)", style=pal["ink-3"])
        self.update(head + body)

    def on_click(self) -> None:
        self.expanded = not self.expanded
        self.refresh_card()


# -------------------------------------------------------------------------------------------------------------------- app

class ChatApp(App):
    CSS_PATH = "chat.tcss"
    TITLE = "SiliconForge"
    BINDINGS = [Binding("shift+tab", "cycle_mode", "Mode", priority=True), Binding("escape", "interrupt", "Interrupt"),
                Binding("ctrl+b", "toggle_side", "Files"), Binding("ctrl+l", "clear", "Clear"), Binding("f1", "help", "Help")]

    def __init__(self, workspace: Any = None, *, session_factory: SessionFactory | None = None, animate: bool = True,
                 theme: str = DEFAULT, store: Any = None) -> None:
        super().__init__()
        for th in build_themes():  # before the stylesheet is parsed: it refers to the theme's variables ($chrome, $layer-rtl, ...)
            self.register_theme(th)
        self.theme = theme
        self.ws = workspace
        self.session_factory = session_factory
        self.animate_splash = animate
        self.start_theme = theme
        self.session_store = store
        self.session: AgentSession | None = None
        self.busy = False
        self.cards: list[ToolCard] = []
        self.turns = 0
        self.tool_calls = 0
        self.tool_errors = 0
        self._tick = 0
        self._always: set[str] = set()

    # ---- layout
    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        with Horizontal(id="body"):
            with Vertical(id="side"):
                yield Static("", id="ws-name")
                yield DirectoryTree(str(self.ws.root) if self.ws else ".", id="tree")
                yield Static("", id="stats")
            with Vertical(id="main"):
                yield VerticalScroll(id="chat")
                yield Input(placeholder="Ask for a change, or type / for commands", id="composer",
                            suggester=SuggestFromList(SLASH, case_sensitive=False))
        yield Footer()

    @property
    def mode_name(self) -> str:
        return "dark" if self.theme == "siliconforge-dark" else "light"

    def on_mount(self) -> None:
        self.session = self.session_factory(self._on_event_thread, self._approve_thread) if self.session_factory else None
        if self.ws:
            self.query_one("#ws-name", Static).update(Text(f"{self.ws.name or Path(self.ws.root).name}", style="bold"))
        self.set_interval(0.1, self._spin)
        self._status()
        self._notice("Describe a change, or ask a question about this folder. `/help` lists the keys and commands.")
        if self.animate_splash:
            self.push_screen(Splash(self.mode_name), lambda _: self.query_one("#composer", Input).focus())
        else:
            self.query_one("#composer", Input).focus()

    # ---- status
    def _status(self) -> None:
        pal = palette(self.mode_name)
        s = self.session
        mode = s.mode if s else "ask"
        stamp = "░ simulated" if (self.ws and self.ws.backend == "simulated") else "● real"
        tok = cost = 0
        stage = "full"
        if s is not None:
            tk = s.router.cost_tracker
            tok, cost, stage = tk.total_tokens, tk.total_cost_usd, s.compaction_stage
        line = Text.assemble((f" {mode} ", mode_style(mode, pal)), "  ", (getattr(self.ws, "profile", "") or "", pal["ink-2"]), "  ",
                             (stamp, f"bold {pal['ink']}"), "  ", (f"{tok:,} tok", pal["ink-3"]), "  ", (f"${cost:.4f}", pal["ink-3"]),
                             "  ", (f"context: {stage}", pal["ink-3"]))
        if self.busy:
            line.append("  working…", style=f"bold {pal['accent']}")
        self.query_one("#status", Static).update(line)
        calls = self.tool_calls
        ok = f"{(calls - self.tool_errors) / calls:.0%}" if calls else "-"
        self.query_one("#stats", Static).update(Text(f"turns {self.turns}\ntool calls {calls}\ntool success {ok}", style=pal["ink-3"]))

    def _spin(self) -> None:
        self._tick += 1
        for c in self.cards:
            c.tick(self._tick)

    # ---- conversation
    def _scroll(self) -> None:
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

    def _add(self, widget: Static | Markdown) -> None:
        self.query_one("#chat", VerticalScroll).mount(widget)
        self.call_after_refresh(self._scroll)

    def _notice(self, text: str, style: str = "") -> None:
        w = Markdown(text) if not style else Static(Text(text, style=style))
        w.add_class("notice")
        w.raw = text  # type: ignore[attr-defined]  # the plain text, for tests and for copying
        self._add(w)

    def _user(self, text: str) -> None:
        pal = palette(self.mode_name)
        w = Static(Text.assemble(("› ", f"bold {pal['accent']}"), text))
        w.add_class("user")
        w.raw = text  # type: ignore[attr-defined]
        self._add(w)

    def _assistant(self, text: str) -> None:
        w = Markdown(text)
        w.add_class("assistant")
        w.raw = text  # type: ignore[attr-defined]
        self._add(w)

    # ---- events from the agent thread
    def _on_event_thread(self, ev: AgentEvent) -> None:
        if threading.current_thread() is threading.main_thread():
            self._on_event(ev)
        else:
            self.call_from_thread(self._on_event, ev)

    def _on_event(self, ev: AgentEvent) -> None:
        pal = palette(self.mode_name)
        if ev.kind == "assistant" and ev.text:
            self._assistant(ev.text)
        elif ev.kind == "tool_call":
            name, args = ev.data.get("tool", ""), ev.data.get("args", {})
            spec = self.session.tools.by_name().get(name) if self.session else None
            card = ToolCard(name, args, spec.permission if spec else "read", diffview.edit_diff(self.ws.root, name, args) if self.ws else [], self.mode_name)
            self.cards.append(card)
            self.tool_calls += 1
            self._add(card)
            card.refresh_card()
        elif ev.kind == "tool_result":
            card = next((c for c in reversed(self.cards) if c.state == "running"), None)
            if card is not None:
                bad = ev.text.startswith(("error:", "denied:", "blocked"))
                if bad:
                    self.tool_errors += 1
                card.finish("denied" if ev.text.startswith(("denied:", "blocked")) else "error" if bad else "ok", ev.text)
        elif ev.kind == "approval":
            card = next((c for c in reversed(self.cards) if c.state == "running"), None)
            if card is not None:
                card.finish("denied", ev.text)
        elif ev.kind == "error":
            self._notice(ev.text, style=pal["fail"])
        self._status()

    def _approve_thread(self, kind: str, summary: str, details: dict) -> bool:
        tool = details.get("tool", "")
        if tool in self._always:
            return True
        done, holder = threading.Event(), {"answer": "no"}

        def ask() -> None:
            diff = diffview.edit_diff(self.ws.root, tool, details.get("args", {})) if self.ws else []

            def got(answer: str | None) -> None:
                holder["answer"] = answer or "no"
                done.set()

            self.push_screen(ApprovalModal(kind, summary, details, diff, self.mode_name), got)

        self.call_from_thread(ask)
        done.wait()
        if holder["answer"] == "always":
            self._always.add(tool)
        return holder["answer"] in ("yes", "always")

    # ---- input
    @on(Input.Submitted, "#composer")
    def submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self.slash(text)
            return
        if self.session is None:
            self._notice("No agent session: open a workspace with `siliconforge tui <folder>`.", style=palette(self.mode_name)["fail"])
            return
        if self.busy:
            self._notice("The agent is working. Press Esc to interrupt it first.", style=palette(self.mode_name)["ink-3"])
            return
        self._user(text)
        self.busy = True
        self._status()
        self.run_worker(lambda: self._turn(text), thread=True, exclusive=True, name="turn")

    def _turn(self, text: str) -> None:
        try:
            self.session.send(text)  # type: ignore[union-attr]
        finally:
            self.call_from_thread(self._turn_done)

    def _turn_done(self) -> None:
        self.busy = False
        self.turns += 1
        self._status()

    # ---- slash commands
    def slash(self, line: str) -> None:
        cmd, _, rest = line.partition(" ")
        rest = rest.strip()
        s = self.session
        pal = palette(self.mode_name)
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/help":
            self._notice(HELP)
        elif cmd == "/clear":
            self.action_clear()
        elif cmd == "/theme":
            self.theme = "siliconforge-light" if self.theme == "siliconforge-dark" else "siliconforge-dark"
        elif s is None:
            self._notice("No agent session for that command.", style=pal["fail"])
        elif cmd == "/mode":
            if rest in MODES:
                s.mode = rest  # type: ignore[assignment]
                self._notice(f"mode: **{rest}**, {MODE_BLURB[rest]}")
            else:
                self._notice("modes: " + ", ".join(f"`{m}` ({MODE_BLURB[m]})" for m in MODES))
            self._status()
        elif cmd == "/undo":
            self._notice(s.tools.undo())
        elif cmd == "/memory":
            from services.orchestrator.agent.memory import Memory

            notes = Memory(s.ws.state_dir).notes()
            self._notice("\n".join(f"- {n}" for n in notes) if notes else "No project memory yet. Set `memory: true` in siliconforge.yaml and the agent can save notes.")
        elif cmd == "/cost":
            tk = s.router.cost_tracker
            self._notice(f"**{tk.total_tokens:,}** tokens, **${tk.total_cost_usd:.4f}**, cache hit rate {tk.cache_hit_rate:.0%}, "
                         f"tool success {(self.tool_calls - self.tool_errors)}/{self.tool_calls}")
        elif cmd == "/sessions":
            if self.session_store is None:
                self._notice("Sessions are not being saved. Start with `siliconforge tui --save`.")
            else:
                rows = self.session_store.list()
                self._notice("\n".join(f"`{i.id}` {i.turns} turn(s): {i.first_message}" for i in rows) or "No saved sessions yet.")
        elif cmd == "/resume":
            if self.session_store is None:
                self._notice("Sessions are not being saved. Start with `siliconforge tui --save`.")
            else:
                try:
                    sid = s.save_to(self.session_store, None if rest in ("", "latest") else rest, resume=True)
                except (FileNotFoundError, ValueError) as exc:
                    self._notice(str(exc), style=pal["fail"])
                else:
                    self._notice(f"resumed `{sid}` with {len(s.history)} entries; the model has what it saw before")
        else:
            self._notice(f"Unknown command `{cmd}`. `/help` lists them.", style=pal["fail"])

    # ---- actions
    def action_cycle_mode(self) -> None:
        if self.session is None:
            return
        i = MODES.index(self.session.mode)
        self.session.mode = MODES[(i + 1) % len(MODES)]  # type: ignore[assignment]
        self._status()

    def action_interrupt(self) -> None:
        if self.busy and self.session is not None:
            self.session.interrupt.set()
            self._notice("Interrupting after the current step…", style=palette(self.mode_name)["ink-3"])

    def action_toggle_side(self) -> None:
        self.query_one("#side").toggle_class("hidden")

    def action_clear(self) -> None:
        self.query_one("#chat", VerticalScroll).remove_children()
        self.cards.clear()

    def action_help(self) -> None:
        self._notice(HELP)
