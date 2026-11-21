# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP API: runs, live SSE streaming, artifacts, auth, settings, providers (simulated backend, replay profile)."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from services.orchestrator.api.app import create_app
from tests.conftest import EXAMPLES


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", serve_ui=False, runs_root=tmp_path / "runs")
    with TestClient(app) as c:
        yield c


def _spec(name="axi_lite_alu") -> str:
    return (EXAMPLES / name / "spec.md").read_text(encoding="utf-8")


def _wait(client, run_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
            return run
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_health_examples_and_defaults(client):
    assert client.get("/api/health").json()["status"] == "ok"
    names = {e["name"] for e in client.get("/api/examples").json()}
    assert {"axi_lite_alu", "uart_tx", "sync_fifo", "up_down_counter"} <= names
    assert "clock_period_ns" in client.get("/api/config/defaults").json()["targets"]


def test_full_run_with_streaming_events_and_artifacts(client):
    run_id = client.post("/api/runs", json={"spec_text": _spec(), "profile": "replay", "backend": "simulated",
                                            "inject_bug": True}).json()["run_id"]
    run = _wait(client, run_id)
    assert run["status"] == "succeeded", run["error"]
    assert run["module"] == "axi_lite_alu" and run["signoff"] == 1 and run["backend"] == "simulated"
    assert run["state"]["eda_mode"] == "simulated" and len(run["state"]["formal_history"]) == 2

    # SSE replays the full ordered stream and ends with `done`
    with client.stream("GET", f"/api/runs/{run_id}/events") as r:
        body = "".join(r.iter_text())
    ids = [int(line[3:]) for line in body.splitlines() if line.startswith("id:")]
    assert ids == sorted(ids) and len(ids) >= 8
    assert "event: done" in body and '"stage": "LLM"' in body  # agent log is streamed too

    # resume after a given event id returns only later events
    tail = client.get(f"/api/runs/{run_id}/events/all").json()
    assert tail[0]["seq"] == 0 and {e["stage"] for e in tail} >= {"ARCHITECT", "FORMAL_VERIFY", "SIGNOFF", "LLM"}

    arts = {a["path"] for a in client.get(f"/api/runs/{run_id}/artifacts").json()}
    assert "signoff_report.json" in arts and any(p.endswith("_formal.sv") for p in arts)
    rep = client.get(f"/api/runs/{run_id}/artifacts/signoff_report.json")
    assert rep.status_code == 200 and json.loads(rep.content)["eda_mode"] == "simulated"

    assert client.get("/api/cost/summary").json()["runs"] == 1
    assert client.delete(f"/api/runs/{run_id}").json()["deleted"]
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_unknown_spec_fails_cleanly_and_is_recorded(client):
    run_id = client.post("/api/runs", json={"spec_text": "Design a RISC-V core with caches.",
                                            "profile": "replay", "backend": "simulated"}).json()["run_id"]
    run = _wait(client, run_id)
    assert run["status"] == "failed" and "replay fixture" in run["error"]


def test_real_backend_without_tools_is_reported_not_mocked(client):
    run_id = client.post("/api/runs", json={"spec_text": _spec("uart_tx"), "profile": "replay",
                                            "backend": "docker"}).json()["run_id"]
    run = _wait(client, run_id)
    assert run["status"] == "failed" and "missing required tools" in run["error"]


def test_validation_and_path_traversal(client):
    assert client.post("/api/runs", json={"spec_text": "x"}).status_code == 422
    assert client.post("/api/runs", json={"spec_text": _spec(), "backend": "quantum"}).status_code == 400
    run_id = client.post("/api/runs", json={"spec_text": _spec("uart_tx"), "profile": "replay"}).json()["run_id"]
    _wait(client, run_id)
    assert client.get(f"/api/runs/{run_id}/artifacts/..%2F..%2Fsiliconforge.db").status_code in (404, 400)
    assert client.get(f"/api/runs/{run_id}/artifacts/../../t.db").status_code in (404, 400)
    assert client.get("/api/runs/nope/artifacts").status_code == 404


def test_token_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("SILICONFORGE_API_TOKEN", "s3cret")
    app = create_app(db_path=tmp_path / "a.db", serve_ui=False, runs_root=tmp_path / "runs")
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200  # liveness stays open for the desktop supervisor
        assert c.get("/api/runs").status_code == 401
        assert c.get("/api/runs", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/runs?token=s3cret").status_code == 200  # EventSource cannot set headers


def test_settings_providers_skills_estimate(client):
    s = client.put("/api/settings", json={"default_profile": "hybrid", "timing_corner": "tt"}).json()
    assert s["default_profile"] == "hybrid" and client.get("/api/settings").json()["timing_corner"] == "tt"
    assert client.put("/api/settings", json={"bogus": 1}).status_code == 400

    p = client.get("/api/providers").json()
    names = {x["name"] for x in p["providers"]}
    assert {"llamacpp", "anthropic", "openai", "gemini", "cli_claude"} <= names
    cli = next(x for x in p["providers"] if x["name"] == "cli_claude")
    assert cli["enabled"] is False and "OAuth" in cli["warning"]
    assert {x["name"] for x in p["profiles"]} >= {"replay", "local", "hybrid", "cloud"}

    assert client.get("/api/estimate?profile=cloud").json()["usd"] > 0
    assert client.get("/api/estimate?profile=nope").status_code == 404

    skills = client.get("/api/skills").json()
    assert skills and client.post("/api/skills/retrieve", json={"query": "sky130 clock budget"}).json()


def test_cancel_a_running_run(client):
    run_id = client.post("/api/runs", json={"spec_text": _spec(), "profile": "replay"}).json()["run_id"]
    client.post(f"/api/runs/{run_id}/cancel")
    run = _wait(client, run_id)
    assert run["status"] in ("cancelled", "succeeded")  # may finish before the cancel lands: both are valid
