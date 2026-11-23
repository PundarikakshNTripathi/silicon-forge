# SPDX-License-Identifier: AGPL-3.0-or-later
"""Workspace agent: protocol parsing, permission modes, sandbox, protected files, undo, transcript compaction.

The model is a scripted fake, so these tests are deterministic and never call a live model or an EDA tool.
"""
from __future__ import annotations

import json

import pytest

from services.orchestrator.agent.session import ACTION_SCHEMA, AgentSession, parse_action
from services.orchestrator.agent.tools import ToolBox, ToolError
from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.catalog import Catalog, Target
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.providers.base import BaseLLMProvider, LLMResponse
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.workspace import init_workspace


class Script(BaseLLMProvider):
    """Replays a list of model replies and records what it was shown."""

    name = "script"
    local = True

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []
        self.schemas: list[dict | None] = []

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        self.prompts.append(user_prompt)
        self.system_prompts.append(system_prompt)
        self.schemas.append(json_schema)
        reply = self.replies.pop(0)
        return LLMResponse(content=reply if isinstance(reply, str) else json.dumps(reply), prompt_tokens=50,
                           completion_tokens=10, model=model, provider="script")


def act(say="", tool=None, **args):
    return {"say": say, "tool": tool, "args": args}


@pytest.fixture
def env(tmp_path):
    ws = init_workspace(tmp_path / "proj", profile="dev", backend="simulated")
    (ws.root / "rtl").mkdir()
    (ws.root / "rtl" / "cnt.sv").write_text("module cnt(input logic clk);\nendmodule\n")
    catalog = Catalog(providers={"script": {"kind": "replay", "local": True}},
                      profiles={"dev": {t: [Target("script", "m")] for t in ("local_fast", "balanced", "frontier_reasoning")}})

    def make(replies, mode="auto", approver=None, **kw):
        prov = Script(replies)
        router = ModelRouter(InferenceConfig(profile="dev", enable_semantic_cache=False), CostTracker(),
                             provider=prov, catalog=catalog)
        events = []
        s = AgentSession(router, ws, ToolBox(ws), mode=mode, approver=approver, on_event=events.append, **kw)
        return s, prov, events

    return ws, make


# ------------------------------------------------------------------------------------------------ protocol

def test_parse_action_tolerates_fences_and_prose():
    assert parse_action('{"say":"hi","tool":null,"args":{}}')["tool"] is None
    assert parse_action('Sure!\n```json\n{"say":"x","tool":"read_file","args":{"path":"a"}}\n```')["tool"] == "read_file"
    assert parse_action('noise {"say":"x","tool":"null","args":null} trailing')["tool"] is None
    with pytest.raises(ValueError):
        parse_action('{"tool": "x"}')


def test_turn_reads_then_answers_and_sends_the_schema(env):
    ws, make = env
    s, prov, events = make([act("looking", "read_file", path="rtl/cnt.sv"), act("It is an empty module.")])
    res = s.send("what is in rtl/cnt.sv?")
    assert res.stopped == "done" and res.reply == "It is an empty module." and res.steps == 2
    assert all(sch == ACTION_SCHEMA for sch in prov.schemas)  # constrained decoding is requested every step
    assert "module cnt" in prov.prompts[1]  # the tool result reached the model
    assert [e.kind for e in events] == ["assistant", "tool_call", "tool_result", "assistant", "done"]


def test_malformed_reply_gets_one_correction_then_fails_cleanly(env):
    ws, make = env
    s, _, _ = make(["not json", act("ok")])
    assert s.send("hi").stopped == "done"
    s2, _, ev = make(["bad", "still bad"])
    r = s2.send("hi")
    assert r.stopped == "error" and ev[-1].kind == "error"


def test_step_limit_stops_a_looping_model(env):
    ws, make = env
    s, _, _ = make([act("", "list_dir", path=".")] * 5, max_steps=3)
    assert s.send("loop").stopped == "step_limit"


# ------------------------------------------------------------------------------------------- permissions

def test_plan_mode_is_read_only(env):
    ws, make = env
    s, _, _ = make([act("", "write_file", path="rtl/new.sv", content="x"), act("done")], mode="plan")
    s.send("add a file")
    assert not (ws.root / "rtl" / "new.sv").exists()
    assert "plan mode is read-only" in s.history[2]["text"]


def test_ask_mode_requires_approval_and_a_decline_stops_the_turn(env):
    ws, make = env
    asked = []
    s, _, _ = make([act("", "write_file", path="a.sv", content="x"), act("unreachable")], mode="ask",
                   approver=lambda kind, summary, details: asked.append(kind) or False)
    r = s.send("write a")
    assert asked == ["edit"] and r.stopped == "denied" and not (ws.root / "a.sv").exists()


def test_ask_mode_with_no_approver_denies_but_lets_the_model_adapt(env):
    ws, make = env
    s, _, _ = make([act("", "write_file", path="a.sv", content="x"), act("I could not write it.")], mode="ask")
    assert s.send("write a").stopped == "done" and not (ws.root / "a.sv").exists()


def test_accept_edits_allows_files_but_still_asks_for_commands(env):
    ws, make = env
    asked = []
    s, _, _ = make([act("", "write_file", path="a.sv", content="module a; endmodule"),
                    {"say": "", "tool": "eda", "args": {"tool": "yosys", "args": ["-V"]}}, act("done")], mode="accept-edits",
                   approver=lambda kind, summary, details: asked.append(kind) or True)
    s.send("go")
    assert (ws.root / "a.sv").exists() and asked == ["command"]


# ---------------------------------------------------------------------------------------------- sandbox

@pytest.mark.parametrize("bad", ["../escape.txt", "rtl/../../escape.txt"])
def test_paths_cannot_leave_the_workspace(env, bad):
    ws, _ = env
    with pytest.raises(ToolError, match="outside the workspace"):
        ToolBox(ws).call("write_file", {"path": bad, "content": "x"})
    with pytest.raises(ToolError, match="outside the workspace"):
        ToolBox(ws).call("read_file", {"path": bad})


@pytest.mark.parametrize("target", ["siliconforge.yaml", ".siliconforge/ai-usage.jsonl", ".git/config"])
def test_agent_cannot_edit_its_own_policy_ledger_or_git(env, target):
    ws, _ = env
    with pytest.raises(ToolError, match="managed by SiliconForge"):
        ToolBox(ws).call("write_file", {"path": target, "content": "allowed_models: []"})


def test_eda_tool_is_allowlisted_and_keeps_arguments_inside(env):
    ws, _ = env
    tb = ToolBox(ws)
    with pytest.raises(ToolError, match="not allowed"):
        tb.call("eda", {"tool": "bash", "args": ["-c", "echo hi"]})
    with pytest.raises(ToolError, match="outside the workspace"):
        tb.call("eda", {"tool": "yosys", "args": ["/etc/passwd"]})
    with pytest.raises(ToolError, match="outside the workspace"):
        tb.call("eda", {"tool": "yosys", "args": ["C:\\Windows\\win.ini"]})


def test_simulated_backend_refuses_to_fake_eda_results(env):
    ws, _ = env
    from services.orchestrator.agent.factory import _Simulated

    tb = ToolBox(ws, runner_factory=_Simulated)
    with pytest.raises(ToolError, match="simulated"):
        tb.call("eda", {"tool": "yosys", "args": ["-V"]})


# ------------------------------------------------------------------------------------- edits and history

def test_edit_requires_a_unique_match_and_undo_restores(env):
    ws, _ = env
    tb = ToolBox(ws)
    with pytest.raises(ToolError, match="was not found"):
        tb.call("edit_file", {"path": "rtl/cnt.sv", "old": "nonexistent", "new": "x"})
    tb.call("edit_file", {"path": "rtl/cnt.sv", "old": "input logic clk", "new": "input logic clk, rst_n"})
    assert "rst_n" in (ws.root / "rtl" / "cnt.sv").read_text()
    tb.call("write_file", {"path": "rtl/new.sv", "content": "x"})
    assert tb.undo().startswith("removed") and not (ws.root / "rtl" / "new.sv").exists()
    assert tb.undo().startswith("restored") and "rst_n" not in (ws.root / "rtl" / "cnt.sv").read_text()
    assert tb.undo() == "nothing to undo"


def test_old_tool_results_are_compacted_but_recent_ones_stay_full(env):
    ws, make = env
    replies = [act("", "read_file", path="rtl/cnt.sv")] * 5 + [act("done")]
    s, prov, _ = make(replies, max_steps=10)
    s.loop_guard.warn_at = s.loop_guard.stop_at = 99  # this test is about compaction; the loop guard has its own tests
    s.send("read it many times")
    last = prov.prompts[-1]
    assert last.count("[earlier result:") == 2 and last.count("module cnt") >= 3  # 5 results: 2 compacted, 3 full


def test_instructions_file_reaches_the_system_prompt(env):
    ws, make = env
    (ws.root / "SILICONFORGE.md").write_text("Always use active-low reset named rst_n.")
    s, _, _ = make([act("ok")])
    assert "active-low reset named rst_n" in s._system() and "SILICONFORGE.md" in s._system()


def test_workspace_instruction_text_cannot_break_the_prompt_template(env):
    ws, make = env
    (ws.root / "AGENTS.md").write_text("Use {braces} and {0} freely: {tools}")
    s, _, _ = make([act("ok")])
    assert "{braces}" in s._system()


# ---------------------------------------------------------------------------------- hardware design tools

PIO16 = "hardware/isa/pio16"


def _copy_pio16(ws):
    import shutil
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "hardware" / "isa" / "pio16"
    shutil.copy(src / "isa.yaml", ws.root / "isa.yaml")
    shutil.copy(src / "uart_tx.asm", ws.root / "uart_tx.asm")


def test_new_tools_are_listed_for_the_model(env):
    ws, _ = env
    names = set(ToolBox(ws).by_name())
    assert {"isa_check", "isa_gen", "sim", "mutate", "tt_check", "tt_synth", "tt_gls"} <= names
    assert "isa_check(" in ToolBox(ws).describe()


def test_isa_check_tool_assembles_and_proves_timing(env):
    ws, _ = env
    _copy_pio16(ws)
    r = ToolBox(ws).call("isa_check", {"spec_path": "isa.yaml", "program_path": "uart_tx.asm"})
    assert r["assembled"] and r["timing_ok"] and len(r["words"]) == 24
    assert sum(1 for g in r["static_gaps"] if g["min"] == g["max"] == 16) >= 10
    (ws.root / "bad.asm").write_text("frobnicate 1\n")
    bad = ToolBox(ws).call("isa_check", {"spec_path": "isa.yaml", "program_path": "bad.asm"})
    assert bad["assembled"] is False and "cannot parse" in bad["errors"][0]
    with pytest.raises(ToolError, match="isa spec error"):
        ToolBox(ws).call("isa_check", {"spec_path": "bad.asm", "program_path": "uart_tx.asm"})


def test_isa_gen_tool_writes_inside_the_workspace_only(env):
    ws, _ = env
    _copy_pio16(ws)
    tb = ToolBox(ws)
    r = tb.call("isa_gen", {"spec_path": "isa.yaml", "out_dir": "gen"})
    assert "gen/rtl/pio16_decode.v" in r["written"] and (ws.root / "gen" / "tools" / "pio16_isa.py").exists()
    with pytest.raises(ToolError, match="outside the workspace"):
        tb.call("isa_gen", {"spec_path": "isa.yaml", "out_dir": "../elsewhere"})
    with pytest.raises(ToolError, match="managed by SiliconForge"):
        tb.call("isa_gen", {"spec_path": "isa.yaml", "out_dir": ".siliconforge/x"})


def test_real_tool_commands_refuse_the_simulated_backend_and_need_approval_in_ask_mode(env):
    ws, make = env
    from services.orchestrator.agent.factory import _Simulated

    tb = ToolBox(ws, runner_factory=_Simulated)
    for name, args in (("sim", {"sources": ["a.v"], "top": "t", "test_module": "m"}),
                       ("mutate", {"files": ["a.v"], "test_command": "true"}), ("tt_synth", {}), ("tt_gls", {})):
        with pytest.raises(ToolError, match="simulated"):
            tb.call(name, args)
    asked = []
    s, _, _ = make([act("", "sim", sources=["a.v"], top="t", test_module="m"), act("done")], mode="ask",
                   approver=lambda kind, summary, details: asked.append(kind) or False)
    assert s.send("simulate").stopped == "denied" and asked == ["command"]


def test_interrupt_stops_the_turn_before_the_next_model_call(env):
    """Esc in a UI sets `interrupt` from another thread; the turn ends cleanly instead of making another model call."""
    ws, make = env
    s, prov, events = make([act("looking", "list_dir", path="."), act("never reached")])
    original = s._run_tool

    def run_then_interrupt(name, args):
        stop = original(name, args)
        s.interrupt.set()  # the user pressed Esc while the first tool was running
        return stop

    s._run_tool = run_then_interrupt  # type: ignore[method-assign]
    res = s.send("do the thing")
    assert res.stopped == "interrupted" and res.steps == 1
    assert len(prov.prompts) == 1  # the second model call never happened
    assert any(e.kind == "error" and e.text == "interrupted" for e in events)
    # the flag does not leak into the next turn
    s._run_tool = original  # type: ignore[method-assign]
    assert s.send("again").stopped == "done"


def test_a_vague_first_request_is_flagged_to_the_model_once_and_a_concrete_one_is_not(env):
    ws, make = env
    s, prov, _ = make([act("Which do you mean: clock speed, latency or area?")])
    s.send("Make the design faster.")
    assert "underspecified" in prov.prompts[0]  # the reminder reached the model
    s2, prov2, _ = make([act("Done.")])
    s2.send("Add a comment to rtl/cnt.sv saying it is reviewed.")
    assert "underspecified" not in prov2.prompts[0]
    s3, prov3, _ = make([act("ok"), act("ok")])
    s3.send("List the files.")
    s3.send("Make it better.")  # a later turn: the user may be answering a question, so it is not held again
    assert "underspecified" not in prov3.prompts[1]


def test_ask_first_can_be_turned_off_to_reproduce_the_released_loop(env):
    ws, make = env
    s, prov, _ = make([act("ok")], ask_first=False)
    s.send("Make the design faster.")
    assert "underspecified" not in prov.prompts[0] and "ambiguous" not in prov.system_prompts[0]
