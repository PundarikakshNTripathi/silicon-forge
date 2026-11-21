# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hooks: parsing is strict, a failing or missing pre hook blocks, a post hook's output reaches the model."""
from __future__ import annotations

import json
import sys

import pytest

from services.orchestrator.agent.hooks import HookError, HookSet
from services.orchestrator.agent.session import AgentSession
from services.orchestrator.agent.tools import ToolBox
from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.catalog import Catalog, Target
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.providers.base import BaseLLMProvider, LLMResponse
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.workspace import init_workspace, load_workspace

PY = sys.executable


def hook(event, match, code, timeout=30):
    return {"event": event, "match": match, "run": [PY, "-c", code], "timeout": timeout}


# ------------------------------------------------------------------------------------------------- parsing

@pytest.mark.parametrize("bad, msg", [
    ([{"event": "during", "match": "x", "run": ["a"]}], "event must be one of"),
    ([{"event": "pre_tool", "match": "write_file(rtl/*", "run": ["a"]}], "match must be"),
    ([{"event": "pre_tool", "match": "x", "run": "python check.py"}], "argument list, not a shell line"),
    ([{"event": "pre_tool", "match": "x", "run": []}], "non-empty list"),
    ([{"event": "pre_tool", "match": "x", "run": ["a"], "timeout": 0}], "between 0 and 600"),
    ([{"event": "pre_tool", "match": "x", "run": ["a"], "timeout": "soon"}], "number of seconds"),
    (["not a mapping"], "expected a mapping"),
])
def test_bad_hook_definitions_are_errors(bad, msg):
    with pytest.raises(HookError, match=msg):
        HookSet.parse(bad)


def test_matching_uses_tool_and_glob():
    hs = HookSet.parse([hook("post_tool", "write_file(rtl/*)", "pass"), hook("post_tool", "eda", "pass")])
    a, b = hs.hooks
    assert a.matches("write_file", {"path": "rtl/x.sv"}) and not a.matches("write_file", {"path": "tb/x.sv"})
    assert not a.matches("edit_file", {"path": "rtl/x.sv"})
    assert b.matches("eda", {"tool": "yosys", "args": []})


# --------------------------------------------------------------------------------------------------- running

def test_pre_hook_exit_status_blocks_and_carries_its_output(tmp_path):
    hs = HookSet.parse([hook("pre_tool", "write_file", "import sys; print('no writes before lint'); sys.exit(3)")])
    out = hs.run("pre_tool", "write_file", {"path": "a.sv"}, tmp_path)
    assert out.blocked and "exited 3" in out.output and "no writes before lint" in out.output
    ok = HookSet.parse([hook("pre_tool", "write_file", "print('fine')")]).run("pre_tool", "write_file", {"path": "a"}, tmp_path)
    assert not ok.blocked and ok.output == "fine"


def test_a_pre_hook_that_cannot_run_or_times_out_blocks(tmp_path):
    missing = HookSet.parse([{"event": "pre_tool", "match": "eda", "run": ["no-such-program-xyz"]}])
    r = missing.run("pre_tool", "eda", {"tool": "yosys", "args": []}, tmp_path)
    assert r.blocked and "could not start" in r.output
    slow = HookSet.parse([hook("pre_tool", "eda", "import time; time.sleep(5)", timeout=0.3)])
    r = slow.run("pre_tool", "eda", {"tool": "yosys", "args": []}, tmp_path)
    assert r.blocked and "timed out" in r.output


def test_a_failing_post_hook_reports_but_does_not_block(tmp_path):
    hs = HookSet.parse([hook("post_tool", "write_file", "import sys; print('style: 2 issues'); sys.exit(1)")])
    r = hs.run("post_tool", "write_file", {"path": "a.sv"}, tmp_path, result="wrote")
    assert not r.blocked and "exited 1" in r.output and "style: 2 issues" in r.output


def test_the_hook_sees_the_call_in_its_environment_and_runs_in_the_workspace(tmp_path):
    code = ("import os, json, pathlib; pathlib.Path('seen.json').write_text(json.dumps({k: os.environ[k] for k in "
            "('SF_HOOK_EVENT','SF_TOOL','SF_ARGS','SF_SUBJECT','SF_RESULT_HEAD')}))")
    HookSet.parse([hook("post_tool", "write_file", code)]).run("post_tool", "write_file", {"path": "rtl/../a.sv"}, tmp_path,
                                                               result="wrote 5 bytes")
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert seen["SF_HOOK_EVENT"] == "post_tool" and seen["SF_TOOL"] == "write_file" and seen["SF_SUBJECT"] == "a.sv"
    assert json.loads(seen["SF_ARGS"]) == {"path": "rtl/../a.sv"} and seen["SF_RESULT_HEAD"] == "wrote 5 bytes"


def test_the_first_blocking_pre_hook_stops_later_ones(tmp_path):
    hs = HookSet.parse([hook("pre_tool", "x", "import sys; sys.exit(1)"),
                        hook("pre_tool", "x", "import pathlib; pathlib.Path('second.txt').write_text('ran')")])
    assert hs.run("pre_tool", "x", {}, tmp_path).blocked and not (tmp_path / "second.txt").exists()


# ------------------------------------------------------------------------------------- workspace and session

def test_manifest_hooks_load_and_a_non_list_is_an_error(tmp_path):
    (tmp_path / "siliconforge.yaml").write_text(
        "name: p\nhooks:\n  - event: post_tool\n    match: write_file(rtl/*)\n    run: [python, check.py]\n", encoding="utf-8")
    assert load_workspace(tmp_path).hooks[0]["run"] == ["python", "check.py"]
    (tmp_path / "siliconforge.yaml").write_text("name: p\nhooks: nope\n", encoding="utf-8")
    with pytest.raises(Exception, match="hooks"):
        load_workspace(tmp_path)


class Script(BaseLLMProvider):
    name = "script"
    local = True

    def __init__(self, replies):
        self.replies = list(replies)

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        return LLMResponse(content=json.dumps(self.replies.pop(0)), prompt_tokens=5, completion_tokens=5, model=model, provider="script")


def act(say="", tool=None, **args):
    return {"say": say, "tool": tool, "args": args}


@pytest.fixture
def make(tmp_path):
    ws = init_workspace(tmp_path / "p", profile="dev", backend="simulated")
    catalog = Catalog(providers={"script": {"kind": "replay", "local": True}},
                      profiles={"dev": {t: [Target("script", "m")] for t in ("local_fast", "balanced", "frontier_reasoning")}})

    def build(replies, hooks):
        router = ModelRouter(InferenceConfig(profile="dev", enable_semantic_cache=False), CostTracker(), provider=Script(replies),
                             catalog=catalog)
        return AgentSession(router, ws, ToolBox(ws), mode="auto", hooks=HookSet.parse(hooks))

    return ws, build


def test_blocked_call_does_not_run_and_the_model_is_told_why(make):
    ws, build = make
    s = build([act("", "write_file", path="a.sv", content="module a; endmodule"), act("ok")],
              [hook("pre_tool", "write_file(*.sv)", "import sys; print('run lint first'); sys.exit(1)")])
    s.send("write it")
    assert not (ws.root / "a.sv").exists()
    tool_text = [h["text"] for h in s.history if h["role"] == "tool"][0]
    assert "blocked by a pre_tool hook" in tool_text and "run lint first" in tool_text


def test_post_hook_output_is_appended_to_the_tool_result(make):
    ws, build = make
    s = build([act("", "write_file", path="a.sv", content="module a; endmodule"), act("ok")],
              [hook("post_tool", "write_file(*.sv)", "print('style check: clean')")])
    s.send("write it")
    assert (ws.root / "a.sv").exists()
    tool_text = [h["text"] for h in s.history if h["role"] == "tool"][0]
    assert "[post_tool hook]" in tool_text and "style check: clean" in tool_text


def test_post_hook_does_not_run_after_a_failed_call(make):
    ws, build = make
    s = build([act("", "read_file", path="missing.sv"), act("ok")],
              [hook("post_tool", "read_file", "import pathlib; pathlib.Path('post_ran').write_text('x')")])
    s.send("read it")
    assert not (ws.root / "post_ran").exists()
