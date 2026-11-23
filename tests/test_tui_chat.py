# SPDX-License-Identifier: AGPL-3.0-or-later
"""The chat TUI, driven headlessly with Textual's Pilot and a scripted model: no live model, no EDA tool."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

pytest.importorskip("textual")

from rich.text import Text  # noqa: E402

from services.orchestrator.agent.session import AgentSession  # noqa: E402
from services.orchestrator.agent.tools import ToolBox  # noqa: E402
from services.orchestrator.config import InferenceConfig  # noqa: E402
from services.orchestrator.inference.catalog import Catalog, Target  # noqa: E402
from services.orchestrator.inference.cost_tracker import CostTracker  # noqa: E402
from services.orchestrator.inference.providers.base import BaseLLMProvider, LLMResponse  # noqa: E402
from services.orchestrator.inference.router import ModelRouter  # noqa: E402
from services.orchestrator.workspace import init_workspace  # noqa: E402
from services.tui import art, diffview  # noqa: E402
from services.tui.chat import ApprovalModal, ChatApp, ToolCard  # noqa: E402


class Script(BaseLLMProvider):
    name = "script"
    local = True

    def __init__(self, replies, delay=0.0):
        self.replies, self.delay = list(replies), delay

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        if self.delay:
            time.sleep(self.delay)
        reply = self.replies.pop(0)
        return LLMResponse(content=json.dumps(reply), prompt_tokens=40, completion_tokens=8, model=model, provider="script")


def act(say="", tool=None, **args):
    return {"say": say, "tool": tool, "args": args}


def make_app(tmp_path, replies, mode="ask", delay=0.0):
    ws = init_workspace(tmp_path / "proj", profile="dev", backend="simulated")
    (ws.root / "rtl").mkdir()
    (ws.root / "rtl" / "cnt.sv").write_text("module cnt(input logic clk);\nendmodule\n", encoding="utf-8")
    catalog = Catalog(providers={"script": {"kind": "replay", "local": True}},
                      profiles={"dev": {t: [Target("script", "m")] for t in ("local_fast", "balanced", "frontier_reasoning")}})

    def factory(on_event, approver):
        router = ModelRouter(InferenceConfig(profile="dev", enable_semantic_cache=False), CostTracker(),
                             provider=Script(replies, delay), catalog=catalog)
        return AgentSession(router, ws, ToolBox(ws), mode=mode, approver=approver, on_event=on_event)

    return ChatApp(ws, session_factory=factory, animate=False), ws


async def send(app, pilot, text):
    app.query_one("#composer").value = text
    await pilot.press("enter")


async def settle(app, pilot, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        await pilot.pause(0.05)
        if not app.busy and app.turns:
            return
    raise AssertionError("the turn did not finish")


async def wait_for_modal(app, pilot):
    for _ in range(100):
        await pilot.pause(0.05)
        if isinstance(app.screen, ApprovalModal):
            return
    raise AssertionError("an edit in ask mode must open the approval modal")


def run(coro):
    asyncio.run(coro)


def test_a_question_shows_a_tool_card_then_the_answer(tmp_path):
    app, ws = make_app(tmp_path, [act("looking", "read_file", path="rtl/cnt.sv"), act("It is an empty counter module.")])

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await send(app, pilot, "what is in rtl/cnt.sv?")
            await settle(app, pilot)
            cards = list(app.query(ToolCard))
            assert len(cards) == 1 and cards[0].state == "ok" and cards[0].tool == "read_file"
            assert "module cnt" in cards[0].result
            assert app.tool_calls == 1 and app.tool_errors == 0 and app.turns == 1

    run(scenario())


def test_an_edit_is_shown_as_a_diff_and_applied_only_when_approved(tmp_path):
    old, new = "module cnt(input logic clk);", "module cnt(input logic clk, input logic rst_n);"
    edit = act("adding reset", "edit_file", path="rtl/cnt.sv", old=old, new=new)

    async def scenario(answer, expect_first_line, expect_state):
        app, ws = make_app(tmp_path / answer, [edit, act("done")])
        async with app.run_test(size=(120, 40)) as pilot:
            await send(app, pilot, "add a reset")
            await wait_for_modal(app, pilot)
            assert any(ln.startswith("+") and "rst_n" in ln for ln in app.screen.diff)  # the user approves the change, not JSON
            await pilot.press(answer)
            await settle(app, pilot)
            assert (ws.root / "rtl" / "cnt.sv").read_text(encoding="utf-8").splitlines()[0] == expect_first_line
            assert list(app.query(ToolCard))[0].state == expect_state

    run(scenario("y", new, "ok"))
    run(scenario("n", old, "denied"))


def test_always_allow_skips_the_next_prompt(tmp_path):
    app, ws = make_app(tmp_path, [act("one", "write_file", path="a.txt", content="1\n"),
                                  act("two", "write_file", path="b.txt", content="2\n"), act("done")])

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await send(app, pilot, "write two files")
            await wait_for_modal(app, pilot)
            await pilot.press("a")
            await settle(app, pilot)
            assert (ws.root / "a.txt").exists() and (ws.root / "b.txt").exists()  # the second write did not ask again

    run(scenario())


def test_shift_tab_cycles_the_mode_and_slash_commands_work(tmp_path):
    app, ws = make_app(tmp_path, [act("hi")])

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            assert app.session.mode == "ask"
            for expected in ("accept-edits", "auto", "plan", "ask"):
                await pilot.press("shift+tab")
                assert app.session.mode == expected
            await send(app, pilot, "/mode plan")
            await pilot.pause(0.1)
            assert app.session.mode == "plan"
            await send(app, pilot, "/nonsense")
            await pilot.pause(0.1)
            assert "Unknown command" in app.query_one("#chat").children[-1].raw
            assert "plan" in str(app.query_one("#status").render())

    run(scenario())


def test_esc_interrupts_a_running_turn(tmp_path):
    steps = [act(f"step {i}", "list_dir", path=".") for i in range(12)]
    app, ws = make_app(tmp_path, steps + [act("never")], mode="auto", delay=0.15)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await send(app, pilot, "list things forever")
            await pilot.pause(0.5)
            assert app.busy
            await pilot.press("escape")
            await settle(app, pilot, timeout=6)
            assert len(list(app.query(ToolCard))) < 12  # it stopped early

    run(scenario())


def test_input_is_refused_while_the_agent_is_working(tmp_path):
    app, ws = make_app(tmp_path, [act("a", "list_dir", path="."), act("b", "list_dir", path="."), act("done")], mode="auto", delay=0.2)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await send(app, pilot, "first")
            await pilot.pause(0.1)
            await send(app, pilot, "second")
            await pilot.pause(0.1)
            assert any("working" in getattr(w, "raw", "") for w in app.query_one("#chat").children)  # a notice, not a second turn
            await settle(app, pilot)
            assert app.turns == 1

    run(scenario())


def test_splash_deposits_the_five_layers_progressively_in_their_own_hues():
    first, last = art.splash(0.0), art.splash(1.0)
    assert "█" not in first.plain and "SiliconForge" not in first.plain
    assert "SiliconForge" in last.plain and last.plain.count("█") == sum(n for _, _, n, _ in art.BARS)
    count = lambda t: t.plain.count("█")  # noqa: E731
    assert 0 < count(art.splash(0.3)) < count(last)  # progress is monotonic
    colours = {str(span.style) for span in last.spans if "█" in last.plain[span.start:span.end]}
    assert len(colours) >= 5  # five layers, five distinct colours


def test_stage_strip_marks_real_versus_simulated_and_repair():
    states = {"ARCHITECT": "done", "RTL": "done", "GUARDRAIL_AUDIT": "done", "FORMAL_VERIFY": "active"}
    real = art.stage_strip(states, {"FORMAL_VERIFY"}, simulated=False).plain
    sim = art.stage_strip(states, set(), simulated=True).plain
    assert "█" in real and "▒" not in real and "◆" in real  # solid for real tools, a diamond where a stage sent the design back
    assert "▒" in sim and "◆" not in sim  # hatched for a surrogate
    assert isinstance(art.stage_strip({}, set(), True), Text)


def test_diffview_edit_and_write(tmp_path):
    f = tmp_path / "a.sv"
    f.write_text("one\ntwo\nthree\n", encoding="utf-8")
    d = diffview.edit_diff(tmp_path, "edit_file", {"path": "a.sv", "old": "two", "new": "TWO"})
    assert "-two" in d and "+TWO" in d and diffview.counts(d) == (1, 1)
    w = diffview.edit_diff(tmp_path, "write_file", {"path": "new.sv", "content": "x\ny\n"})
    assert diffview.counts(w) == (2, 0)  # a new file is all additions
    assert diffview.edit_diff(tmp_path, "read_file", {"path": "a.sv"}) == []  # only edits have a diff
    missing = diffview.edit_diff(tmp_path, "edit_file", {"path": "a.sv", "old": "nope", "new": "x"})
    assert "-nope" in missing  # an edit that will not apply still shows what was asked for


def test_diff_is_compacted_to_the_changes_and_results_are_readable():
    diff = ["--- a/x", "+++ b/x", "@@ -1,6 +1,6 @@", " a", " b", " c", "-old", "+new", " d", " e", " f"]
    shown = diffview.compact(diff, context=1)
    assert shown == [" c", "-old", "+new", " d"]  # headers and far-away context are gone
    assert diffview.pretty("list_dir", json.dumps({"path": ".", "entries": [{"name": "rtl/"}, {"name": "a.sv"}]})) == ["rtl/  a.sv"]
    read = diffview.pretty("read_file", json.dumps({"path": "a.sv", "total_lines": 2, "shown": [1, 2], "content": "1\tone\n2\ttwo"}))
    assert read[0] == "a.sv: lines 1-2 of 2" and read[1:] == ["1\tone", "2\ttwo"]
    assert diffview.pretty("eda", "error: tool not found") == ["error: tool not found"]  # plain text passes through


def test_splash_runs_to_the_end_before_the_workspace_opens_and_ignores_typing(tmp_path):
    """The splash was cut short by any key (including a command typed while the program started) and by a slow start-up."""
    from services.tui.chat import Splash

    app, _ = make_app(tmp_path, [])
    app.animate_splash = True
    Splash.DRAW_S, Splash.HOLD_S, Splash.SKIP_AFTER_S = 0.6, 0.3, 0.2  # the same logic on a shorter clock

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.05)
            assert isinstance(app.screen, Splash) and app.session is None  # nothing is loaded behind the splash yet
            await pilot.press("x", "y", "z")  # typed ahead
            await pilot.press("enter")  # too early to skip
            await pilot.pause(0.05)
            assert isinstance(app.screen, Splash)
            await pilot.pause(1.2)
            assert not isinstance(app.screen, Splash) and app.session is not None
            assert app.query_one("#composer").value == ""  # nothing typed during the splash reached the composer

    run(scenario())
