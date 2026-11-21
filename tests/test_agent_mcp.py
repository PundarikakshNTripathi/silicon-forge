# SPDX-License-Identifier: AGPL-3.0-or-later
"""MCP client: configuration is strict, tools from a real stdio server become agent tools, and every guard still applies."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from services.orchestrator.agent.mcp_client import (  # noqa: E402
    McpBridge,
    McpConfigError,
    describe_params,
    parse_servers,
    tool_name,
)
from services.orchestrator.agent.safety import Rules  # noqa: E402
from services.orchestrator.agent.session import AgentSession  # noqa: E402
from services.orchestrator.agent.tools import READ, ToolBox  # noqa: E402
from services.orchestrator.config import InferenceConfig  # noqa: E402
from services.orchestrator.inference.catalog import Catalog, Target  # noqa: E402
from services.orchestrator.inference.cost_tracker import CostTracker  # noqa: E402
from services.orchestrator.inference.providers.base import BaseLLMProvider, LLMResponse  # noqa: E402
from services.orchestrator.inference.router import ModelRouter  # noqa: E402
from services.orchestrator.workspace import init_workspace, load_workspace  # noqa: E402

DEMO = str(Path(__file__).with_name("mcp_demo_server.py"))


def spec(name="demo", **kw):
    return {"name": name, "command": [sys.executable, DEMO], **kw}


# ------------------------------------------------------------------------------------------------- parsing

@pytest.mark.parametrize("bad, msg", [
    ([{"name": "Demo", "command": ["x"]}], "name must be lowercase"),
    ([{"name": "a", "command": "python server.py"}], "argument list, not a shell line"),
    ([{"name": "a", "command": []}], "non-empty list"),
    ([{"name": "a", "command": ["x"], "permission": "root"}], "permission must be one of"),
    ([{"name": "a", "command": ["x"], "timeout": 0}], "between 0 and 3600"),
    ([{"name": "a", "command": ["x"], "env": ["PATH"]}], "env must map"),
    ([{"name": "a", "command": ["x"]}, {"name": "a", "command": ["y"]}], "duplicate server name"),
    (["nope"], "expected a mapping"),
])
def test_bad_server_definitions_are_errors(bad, msg):
    with pytest.raises(McpConfigError, match=msg):
        parse_servers(bad)


def test_defaults_are_the_cautious_ones():
    s = parse_servers([{"name": "or", "command": ["x", "y"]}])[0]
    assert s.permission != READ and s.timeout == 120.0 and s.argv == ("x", "y")


def test_parameter_descriptions_come_from_the_schema():
    out = describe_params({"properties": {"a": {"type": "integer", "description": "first"}, "b": {"type": ["string", "null"], "default": "x"}},
                           "required": ["a"]})
    assert out["a"] == "integer, first, required" and out["b"] == "string/null, default 'x', optional"
    assert tool_name("demo", "do.thing") == "mcp__demo__do_thing"


# ---------------------------------------------------------------------------------------- a real stdio server

@pytest.fixture(scope="module")
def bridge(tmp_path_factory):
    b = McpBridge(parse_servers([spec(timeout=20)]), tmp_path_factory.mktemp("ws"))
    status = b.start()
    yield b, status
    b.close()


def test_tools_are_discovered_and_described(bridge):
    b, status = bridge
    assert status == {"demo": "ok: 6 tool(s)"}
    names = {t.name for t in b.tool_specs()}
    assert {"mcp__demo__add", "mcp__demo__shout", "mcp__demo__boom"} <= names
    shout = next(t for t in b.tool_specs() if t.name == "mcp__demo__shout")
    assert "Second line" not in shout.description and shout.description.startswith("[MCP server demo]")


def test_calls_return_text_and_errors_become_tool_errors(bridge):
    from services.orchestrator.agent.tools import ToolError

    b, _ = bridge
    tools = {t.name: t for t in b.tool_specs()}
    assert tools["mcp__demo__add"].fn(a=3) == "5" and tools["mcp__demo__shout"].fn(text="abc") == "ABC"
    with pytest.raises(ToolError):
        tools["mcp__demo__boom"].fn()
    with pytest.raises(ToolError):
        tools["mcp__demo__add"].fn(a="not a number")


def test_a_slow_call_times_out_instead_of_hanging(tmp_path):
    from services.orchestrator.agent.tools import ToolError

    b = McpBridge(parse_servers([spec(timeout=1)]), tmp_path)
    try:
        b.start()
        slow = next(t for t in b.tool_specs() if t.name == "mcp__demo__slow")
        with pytest.raises(ToolError, match="timed out"):
            slow.fn(seconds=6)
    finally:
        b.close()


def test_a_server_that_fails_to_start_is_reported_and_the_others_still_work(tmp_path):
    b = McpBridge(parse_servers([spec("good", timeout=20), {"name": "bad", "command": ["no-such-program-xyz"]}]), tmp_path,
                  start_timeout=20)
    try:
        status = b.start()
        assert status["good"].startswith("ok") and status["bad"].startswith("error")
        assert all(t.name.startswith("mcp__good__") for t in b.tool_specs())
    finally:
        b.close()


def test_close_is_idempotent_and_stops_the_loop(tmp_path):
    b = McpBridge(parse_servers([spec(timeout=20)]), tmp_path)
    b.start()
    thread = b._thread
    b.close()
    b.close()
    assert thread is not None and not thread.is_alive()


# ------------------------------------------------------------------------------------------ through the agent

class Script(BaseLLMProvider):
    name = "script"
    local = True

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        self.prompts.append((system_prompt, user_prompt))
        return LLMResponse(content=json.dumps(self.replies.pop(0)), prompt_tokens=5, completion_tokens=5, model=model, provider="script")


def act(say="", tool=None, **args):
    return {"say": say, "tool": tool, "args": args}


@pytest.fixture
def agent(tmp_path):
    ws = init_workspace(tmp_path / "p", profile="dev", backend="simulated")
    catalog = Catalog(providers={"script": {"kind": "replay", "local": True}},
                      profiles={"dev": {t: [Target("script", "m")] for t in ("local_fast", "balanced", "frontier_reasoning")}})
    built: list[McpBridge] = []

    def make(replies, mode="auto", approver=None, rules=None, permission="command"):
        bridge = McpBridge(parse_servers([spec(timeout=20, permission=permission)]), ws.root)
        bridge.start()
        built.append(bridge)
        tools = ToolBox(ws)
        tools.extra.extend(bridge.tool_specs())
        prov = Script(replies)
        router = ModelRouter(InferenceConfig(profile="dev", enable_semantic_cache=False), CostTracker(), provider=prov, catalog=catalog)
        s = AgentSession(router, ws, tools, mode=mode, approver=approver, rules=rules)
        s.bridge = bridge
        return s, prov

    yield make
    for b in built:
        b.close()


def test_mcp_tools_appear_in_the_prompt_and_run(agent):
    s, prov = agent([act("", "mcp__demo__add", a=40, b=2), act("it is 42")])
    res = s.send("add")
    assert res.stopped == "done"
    system = prov.prompts[0][0]
    assert "mcp__demo__add(a: integer" in system
    assert "42" in prov.prompts[-1][1]


def test_mcp_tools_ask_for_approval_by_default_and_deny_rules_apply(agent):
    asked = []
    s, _ = agent([act("", "mcp__demo__add", a=1), act("ok")], mode="ask", approver=lambda k, summ, d: asked.append(summ) or False)
    s.send("x")
    assert len(asked) == 1 and "mcp__demo__add" in asked[0]

    s2, _ = agent([act("", "mcp__demo__add", a=1), act("ok")], rules=Rules.parse(deny=["mcp__demo__add"]))
    s2.send("x")
    assert any("deny rule" in h["text"] for h in s2.history if h["role"] == "tool")

    s3, _ = agent([act("", "mcp__demo__add", a=1), act("ok")], mode="ask", approver=lambda *a: False,
                  rules=Rules.parse(allow=["mcp__demo__add"]))
    s3.send("x")
    assert [h["text"] for h in s3.history if h["role"] == "tool"][0] == "3"  # allow rule skipped the prompt


def test_plan_mode_refuses_mcp_tools_unless_the_server_is_marked_read(agent):
    s, _ = agent([act("", "mcp__demo__add", a=1), act("ok")], mode="plan")
    s.send("x")
    assert any("plan mode is read-only" in h["text"] for h in s.history if h["role"] == "tool")
    s2, _ = agent([act("", "mcp__demo__add", a=1), act("ok")], mode="plan", permission="read")
    s2.send("x")
    assert not any("plan mode" in h["text"] for h in s2.history if h["role"] == "tool")


def test_server_output_is_redacted_and_fenced_like_any_tool_output(agent):
    s, prov = agent([act("", "mcp__demo__leak"), act("", "mcp__demo__injected"), act("done")])
    s.send("go")
    last = prov.prompts[-1][1]
    assert "k7j6h5g4f3" not in last and "[redacted" in last
    assert last.count("</tool_output>") == last.count("<tool_output tool=")  # the injected closer was defanged


def test_the_session_announces_server_status_once_and_close_stops_the_bridge(agent):
    events = []
    s, _ = agent([act("a"), act("b")])
    s.on_event = events.append
    s.send("one")
    s.send("two")
    assert [e.text for e in events if e.text.startswith("MCP server")] == ["MCP server demo: ok: 6 tool(s)"]
    s.close()
    assert s.bridge._loop is None


def test_manifest_mcp_servers_load_and_a_non_list_is_an_error(tmp_path):
    (tmp_path / "siliconforge.yaml").write_text(
        "name: p\nmcp_servers:\n  - name: demo\n    command: [python, server.py]\n", encoding="utf-8")
    assert load_workspace(tmp_path).mcp_servers[0]["name"] == "demo"
    (tmp_path / "siliconforge.yaml").write_text("name: p\nmcp_servers: nope\n", encoding="utf-8")
    with pytest.raises(Exception, match="mcp_servers"):
        load_workspace(tmp_path)
