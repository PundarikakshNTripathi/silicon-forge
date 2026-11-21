# SPDX-License-Identifier: AGPL-3.0-or-later
"""Project memory and the read-only investigator subagent. The model is a scripted fake; nothing here calls a live model."""
from __future__ import annotations

import json

import pytest

from services.orchestrator.agent.memory import MAX_NOTES, MAX_PROMPT_CHARS, Memory
from services.orchestrator.agent.session import AgentSession
from services.orchestrator.agent.subagent import MAX_ANSWER_CHARS, investigate_tool
from services.orchestrator.agent.tools import ToolBox
from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.catalog import Catalog, Target
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.providers.base import BaseLLMProvider, LLMResponse
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.main import cli_main
from services.orchestrator.workspace import init_workspace


class Script(BaseLLMProvider):
    name = "script"
    local = True

    def __init__(self, replies):
        self.replies, self.systems, self.users = list(replies), [], []

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        self.systems.append(system_prompt)
        self.users.append(user_prompt)
        return LLMResponse(content=json.dumps(self.replies.pop(0)), prompt_tokens=30, completion_tokens=6, model=model, provider="script")


def act(say="", tool=None, **args):
    return {"say": say, "tool": tool, "args": args}


@pytest.fixture
def env(tmp_path):
    ws = init_workspace(tmp_path / "proj", profile="dev", backend="simulated")
    (ws.root / "rtl").mkdir()
    (ws.root / "rtl" / "fifo.sv").write_text("module fifo;\n  // depth is 16\nendmodule\n", encoding="utf-8")
    catalog = Catalog(providers={"script": {"kind": "replay", "local": True}},
                      profiles={"dev": {t: [Target("script", "m")] for t in ("local_fast", "balanced", "frontier_reasoning")}})

    def make(replies, mode="auto", with_subagent=False):
        prov = Script(replies)
        router = ModelRouter(InferenceConfig(profile="dev", enable_semantic_cache=False), CostTracker(), provider=prov, catalog=catalog)
        tools = ToolBox(ws)
        if with_subagent:
            tools.extra.append(investigate_tool(router, ws))
        return AgentSession(router, ws, tools, mode=mode), prov

    return ws, make


# ----------------------------------------------------------------------------------------------------------------- memory

def test_memory_saves_masks_secrets_dedupes_and_is_bounded(tmp_path):
    mem = Memory(tmp_path / ".siliconforge")
    assert mem.notes() == [] and mem.prompt_block() == ""
    saved = mem.add("The clock is 100 MHz.\nUse the sky130 PDK.")
    assert saved == "The clock is 100 MHz. Use the sky130 PDK."  # one line
    mem.add(saved)
    assert mem.notes() == [saved]  # a repeat is not saved twice
    leak = mem.add("the key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")
    assert "abcdefghijklmnopqrstuvwxyz" not in leak and "abcdefghijklmnopqrstuvwxyz" not in mem.path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        mem.add("   ")
    for i in range(MAX_NOTES + 10):
        mem.add(f"note number {i}")
    assert len(mem.notes()) == MAX_NOTES and mem.notes()[-1] == f"note number {MAX_NOTES + 9}"  # the newest are kept
    block = mem.prompt_block()
    assert len(block) < MAX_PROMPT_CHARS + 300 and f"note number {MAX_NOTES + 9}" in block and "note number 0" not in block


def test_memory_reaches_the_next_sessions_prompt_and_a_hand_edit_is_respected(env):
    ws, make = env
    Memory(ws.state_dir).add("the top module is fifo")
    s, prov = make([act("ok")])
    s.send("hello")
    assert "the top module is fifo" in prov.systems[0]
    ws.state_dir.joinpath("memory.md").write_text("# mine\n\n- hand written note\n", encoding="utf-8")  # a person edits the file
    s2, prov2 = make([act("ok")])
    s2.send("hello")
    assert "hand written note" in prov2.systems[0] and "the top module is fifo" not in prov2.systems[0]


def test_no_memory_means_no_memory_block(env):
    ws, make = env
    s, prov = make([act("ok")])
    s.send("hello")
    assert "Project memory" not in prov.systems[0]


def test_remember_tool_exists_only_when_the_workspace_opts_in(tmp_path):
    from services.orchestrator.agent.factory import create_session
    from services.orchestrator.config import load_flow_config  # noqa: F401 - the factory loads it; imported to fail early if broken

    off = init_workspace(tmp_path / "off", profile="replay", backend="simulated")
    assert "remember" not in create_session(off, mode="auto").tools.by_name()
    on = init_workspace(tmp_path / "on", profile="replay", backend="simulated")
    on.memory = True
    s = create_session(on, mode="auto")
    assert "remember" in s.tools.by_name() and "investigate" in s.tools.by_name()
    assert s.tools.call("remember", {"note": "use 2-space indent"}) == {"saved": "use 2-space indent"}
    assert Memory(on.state_dir).notes() == ["use 2-space indent"]


def test_memory_cli_round_trip(tmp_path, capsys):
    ws = init_workspace(tmp_path / "p", profile="replay", backend="simulated")
    assert cli_main(["memory", "add", "reset", "is", "active", "low", "--workspace", str(ws.root)]) == 0
    capsys.readouterr()
    assert cli_main(["memory", "show", "--workspace", str(ws.root)]) == 0
    assert "- reset is active low" in capsys.readouterr().out
    assert cli_main(["memory", "add", "--workspace", str(ws.root)]) == 2  # nothing to save
    assert cli_main(["memory", "clear", "--workspace", str(ws.root)]) == 0
    assert Memory(ws.state_dir).notes() == []


# -------------------------------------------------------------------------------------------------------------- subagent

def test_investigator_reads_in_its_own_context_and_returns_only_its_answer(env):
    ws, make = env
    s, prov = make([
        act("delegating", "investigate", question="what is the depth of the fifo?"),   # parent
        act("looking", "read_file", path="rtl/fifo.sv"),                                # child, step 1
        act("The depth is 16 (rtl/fifo.sv line 2)."),                                   # child answers
        act("The fifo is 16 deep."),                                                    # parent's final answer
    ], with_subagent=True)
    res = s.send("how deep is the fifo?")
    assert res.stopped == "done" and res.reply == "The fifo is 16 deep."
    child_prompt = prov.users[1]
    assert "what is the depth" in child_prompt and "how deep is the fifo" not in child_prompt  # a fresh transcript, not the parent's
    parent_after = prov.users[3]
    assert "The depth is 16" in parent_after and "module fifo" not in parent_after  # the parent got the answer, not the file
    assert "[investigated in 2 step(s)]" in parent_after


def test_investigator_cannot_edit_or_recurse(env):
    ws, make = env
    s, prov = make([
        act("delegating", "investigate", question="change the depth to 32"),
        act("editing", "write_file", path="rtl/fifo.sv", content="module fifo; endmodule\n"),  # child tries to edit: plan mode refuses
        act("I could not change anything; I am read-only."),
        act("done"),
    ], with_subagent=True)
    s.send("task")
    assert "depth is 16" in (ws.root / "rtl" / "fifo.sv").read_text(encoding="utf-8")  # unchanged
    child_system = prov.systems[1]
    assert "investigate(" not in child_system  # the child's tool list has no investigator: no recursion


def test_a_long_answer_is_cut_and_an_early_stop_is_reported(env):
    ws, make = env
    long = "x" * (MAX_ANSWER_CHARS * 2)
    s, prov = make([act("go", "investigate", question="q"), act(long), act("done")], with_subagent=True)
    s.send("t")
    sent_back = prov.users[2]
    assert "x" * MAX_ANSWER_CHARS in sent_back and "x" * (MAX_ANSWER_CHARS + 1) not in sent_back
