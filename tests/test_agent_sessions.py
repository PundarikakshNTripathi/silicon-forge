# SPDX-License-Identifier: AGPL-3.0-or-later
"""Saved agent conversations: written only when asked, resumable with the same transcript, safe against bad ids and torn files."""
from __future__ import annotations

import json

import pytest

from services.orchestrator.agent.session import AgentSession
from services.orchestrator.agent.store import SessionStore
from services.orchestrator.agent.tools import ToolBox
from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.catalog import Catalog, Target
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.providers.base import BaseLLMProvider, LLMResponse
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.workspace import init_workspace


class Script(BaseLLMProvider):
    name = "script"
    local = True

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        self.prompts.append(user_prompt)
        return LLMResponse(content=json.dumps(self.replies.pop(0)), prompt_tokens=5, completion_tokens=5, model=model,
                           provider="script")


def make_session(ws, replies):
    catalog = Catalog(providers={"script": {"kind": "replay", "local": True}},
                      profiles={"dev": {t: [Target("script", "m")] for t in ("local_fast", "balanced", "frontier_reasoning")}})
    prov = Script(replies)
    router = ModelRouter(InferenceConfig(profile="dev", enable_semantic_cache=False), CostTracker(), provider=prov, catalog=catalog)
    return AgentSession(router, ws, ToolBox(ws), mode="auto"), prov


@pytest.fixture
def ws(tmp_path):
    w = init_workspace(tmp_path / "proj", profile="dev", backend="simulated")
    (w.root / "a.txt").write_text("alpha\n")
    return w


def reply(say, tool=None, **args):
    return {"say": say, "tool": tool, "args": args}


def test_nothing_is_written_unless_asked(ws):
    s, _ = make_session(ws, [reply("done")])
    s.send("hello")
    assert not (ws.state_dir / "sessions").exists()


def test_saved_entries_match_the_history_and_survive_a_resume(ws):
    store = SessionStore(ws.state_dir / "sessions")
    s, _ = make_session(ws, [reply("reading", "read_file", path="a.txt"), reply("it says alpha")])
    sid = s.save_to(store)
    s.send("what is in a.txt?")
    assert store.load(sid) == s.history and len(s.history) == 4

    # a new process resumes: the model sees the earlier turn in its prompt
    s2, prov = make_session(ws, [reply("yes")])
    assert s2.save_to(store, resume=True) == sid
    assert s2.history == s.history
    s2.send("and is it ascii?")
    assert "what is in a.txt?" in prov.prompts[0] and "and is it ascii?" in prov.prompts[0]
    assert store.load(sid) == s2.history  # the resumed turn was appended to the same file


def test_resume_without_a_saved_session_is_an_error(ws):
    s, _ = make_session(ws, [])
    with pytest.raises(FileNotFoundError):
        s.save_to(SessionStore(ws.state_dir / "sessions"), resume=True)


def test_ids_cannot_escape_the_directory(ws):
    store = SessionStore(ws.state_dir / "sessions")
    for bad in ("../x", "..\\x", "a/b", ""):
        with pytest.raises(ValueError):
            store.load(bad)


def test_a_torn_last_line_is_skipped_and_newest_is_found(ws):
    store = SessionStore(ws.state_dir / "sessions")
    store.append("20260101-000000", {"role": "user", "text": "old"})
    store.append("20260102-000000", {"role": "user", "text": "newer question"})
    path = store.dir / "20260102-000000.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"role": "assistant", "say": "tru')  # process killed mid-write
    assert [e["role"] for e in store.load("20260102-000000")] == ["user"]
    assert store.latest() == "20260102-000000"
    assert [(i.id, i.turns, i.first_message) for i in store.list()][0] == ("20260102-000000", 1, "newer question")


def test_two_sessions_in_the_same_second_get_different_ids(ws):
    store = SessionStore(ws.state_dir / "sessions")
    first = store.new_id()
    store.append(first, {"role": "user", "text": "x"})
    assert store.new_id() != first
