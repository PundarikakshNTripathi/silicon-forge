# SPDX-License-Identifier: AGPL-3.0-or-later
"""Workspace + agent-session HTTP API: folder browser, confinement, streaming events, and the approval round trip."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from services.orchestrator.api.agent_manager import AgentManager
from services.orchestrator.api.app import create_app
from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference.catalog import Catalog, Target
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.router import ModelRouter
from tests.test_agent import Script, act


class ScriptedManager(AgentManager):
    """Gives every new session a scripted model instead of a real one."""

    def __init__(self, replies):
        super().__init__(approval_timeout_s=5)
        self.replies = replies

    def create(self, workspace, profile=None, mode="ask", **kw):
        catalog = Catalog(providers={"script": {"kind": "replay", "local": True}},
                          profiles={"dev": {t: [Target("script", "m")] for t in ("local_fast", "balanced", "frontier_reasoning")}})
        router = ModelRouter(InferenceConfig(profile="dev", enable_semantic_cache=False), CostTracker(),
                             provider=Script(self.replies), catalog=catalog)
        return super().create(workspace, "dev", mode, router=router, **kw)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SILICONFORGE_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path


def make_client(home, replies):
    app = create_app(db_path=home / "t.db", serve_ui=False, runs_root=home / "runs", agents=ScriptedManager(replies))
    return TestClient(app)


def wait_for(client, sid, kind, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        evs = client.get(f"/api/agent/sessions/{sid}/events").json()["events"]
        hit = [e for e in evs if e["kind"] == kind]
        if hit:
            return hit[-1], evs
        time.sleep(0.05)
    raise AssertionError(f"no '{kind}' event; saw {[e['kind'] for e in evs]}")


def open_ws(client, home, name="proj"):
    r = client.post("/api/workspaces/open", json={"path": str(home / name), "create": True, "backend": "simulated"})
    assert r.status_code == 200, r.text
    return home / name


def test_open_creates_a_workspace_and_remembers_it(home):
    with make_client(home, []) as c:
        assert c.get("/api/workspaces").json()["recent"] == []
        root = open_ws(c, home)
        assert (root / "siliconforge.yaml").exists()
        recent = c.get("/api/workspaces").json()["recent"]
        assert recent[0]["path"] == str(root.resolve())
        assert c.post("/api/workspaces/open", json={"path": str(home / "nope")}).status_code == 400


def test_folder_browser_lists_directories_only_and_flags_workspaces(home):
    with make_client(home, []) as c:
        root = open_ws(c, home)
        (root / "rtl").mkdir()
        (root / "notes.txt").write_text("x")
        top = c.get("/api/fs", params={"path": str(home)}).json()
        assert [e["name"] for e in top["entries"] if e["name"] == "proj"] == ["proj"]
        assert next(e for e in top["entries"] if e["name"] == "proj")["workspace"] is True
        inner = c.get("/api/fs", params={"path": str(root)}).json()
        assert [e["name"] for e in inner["entries"]] == ["rtl"] and inner["workspace"] is True  # no files listed
        assert c.get("/api/fs", params={"path": str(root / "notes.txt")}).status_code == 404
        assert c.get("/api/fs").json()["roots"]


def test_file_endpoints_are_confined_to_the_workspace(home):
    with make_client(home, []) as c:
        root = open_ws(c, home)
        (root / "rtl").mkdir()
        (root / "rtl" / "a.sv").write_text("module a; endmodule")
        assert c.get("/api/workspace/file", params={"root": str(root), "path": "rtl/a.sv"}).json()["content"].startswith("module a")
        for bad in ("../secret.txt", "rtl/../../secret.txt"):
            assert c.get("/api/workspace/file", params={"root": str(root), "path": bad}).status_code == 400
        names = [e["name"] for e in c.get("/api/workspace/tree", params={"root": str(root)}).json()["entries"]]
        assert "rtl/" in names and ".siliconforge/" not in names


def test_agent_turn_streams_events_and_ends(home):
    with make_client(home, [act("checking", "list_dir", path="."), act("The folder is empty.")]) as c:
        root = open_ws(c, home)
        sid = c.post("/api/agent/sessions", json={"workspace": str(root), "mode": "auto"}).json()["id"]
        assert c.post(f"/api/agent/sessions/{sid}/messages", json={"text": "what is here?"}).status_code == 202
        end, evs = wait_for(c, sid, "turn_end")
        assert end["data"]["stopped"] == "done" and end["text"] == "The folder is empty."
        kinds = [e["kind"] for e in evs]
        assert kinds[0] == "user" and "tool_call" in kinds and "tool_result" in kinds
        assert [e["seq"] for e in evs] == list(range(len(evs)))  # gapless, so a client can resume with ?after=


def test_write_needs_approval_and_allow_completes_the_turn(home):
    replies = [act("", "write_file", path="a.sv", content="module a; endmodule"), act("Wrote a.sv.")]
    with make_client(home, replies) as c:
        root = open_ws(c, home)
        sid = c.post("/api/agent/sessions", json={"workspace": str(root), "mode": "ask"}).json()["id"]
        c.post(f"/api/agent/sessions/{sid}/messages", json={"text": "write a.sv"})
        req, _ = wait_for(c, sid, "approval_request")
        assert req["data"]["permission"] == "edit" and not (root / "a.sv").exists()  # nothing happens before consent
        assert c.post(f"/api/agent/sessions/{sid}/approvals/{req['data']['id']}", json={"allow": True}).json()["answered"]
        end, _ = wait_for(c, sid, "turn_end")
        assert end["data"]["stopped"] == "done" and (root / "a.sv").read_text() == "module a; endmodule"
        assert c.post(f"/api/agent/sessions/{sid}/undo").json()["result"].startswith("removed")
        assert not (root / "a.sv").exists()


def test_declining_an_approval_leaves_files_untouched(home):
    with make_client(home, [act("", "write_file", path="a.sv", content="x"), act("unreachable")]) as c:
        root = open_ws(c, home)
        sid = c.post("/api/agent/sessions", json={"workspace": str(root), "mode": "ask"}).json()["id"]
        c.post(f"/api/agent/sessions/{sid}/messages", json={"text": "write"})
        req, _ = wait_for(c, sid, "approval_request")
        c.post(f"/api/agent/sessions/{sid}/approvals/{req['data']['id']}", json={"allow": False})
        end, _ = wait_for(c, sid, "turn_end")
        assert end["data"]["stopped"] == "denied" and not (root / "a.sv").exists()


def test_second_message_while_busy_is_rejected_and_unknown_session_404s(home):
    with make_client(home, [act("", "write_file", path="a.sv", content="x"), act("done")]) as c:
        root = open_ws(c, home)
        sid = c.post("/api/agent/sessions", json={"workspace": str(root), "mode": "ask"}).json()["id"]
        c.post(f"/api/agent/sessions/{sid}/messages", json={"text": "write"})
        req, _ = wait_for(c, sid, "approval_request")  # the turn is parked waiting for the user
        assert c.post(f"/api/agent/sessions/{sid}/messages", json={"text": "again"}).status_code == 409
        c.post(f"/api/agent/sessions/{sid}/approvals/{req['data']['id']}", json={"allow": True})
        wait_for(c, sid, "turn_end")
        assert c.post("/api/agent/sessions/nope/messages", json={"text": "x"}).status_code == 404
        assert c.put(f"/api/agent/sessions/{sid}/mode", json={"mode": "bogus"}).status_code == 400
        assert c.put(f"/api/agent/sessions/{sid}/mode", json={"mode": "plan"}).json()["mode"] == "plan"


def test_an_unanswered_approval_times_out_as_a_decline(home):
    mgr = ScriptedManager([act("", "write_file", path="a.sv", content="x"), act("unreachable")])
    mgr.approval_timeout_s = 0.3
    app = create_app(db_path=home / "t.db", serve_ui=False, runs_root=home / "runs", agents=mgr)
    with TestClient(app) as c:
        root = open_ws(c, home)
        sid = c.post("/api/agent/sessions", json={"workspace": str(root), "mode": "ask"}).json()["id"]
        c.post(f"/api/agent/sessions/{sid}/messages", json={"text": "write"})
        end, _ = wait_for(c, sid, "turn_end")
        assert end["data"]["stopped"] == "denied" and not (root / "a.sv").exists()
