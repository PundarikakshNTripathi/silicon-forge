# SPDX-License-Identifier: AGPL-3.0-or-later
"""Span storage, the Prometheus text format, and the metrics and insights endpoints."""
from __future__ import annotations

import re
import time

import pytest

from services.orchestrator.api.store import Store
from services.orchestrator.telemetry import Span, prometheus

SAMPLE = re.compile(r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{([a-zA-Z_][a-zA-Z0-9_]*="([^"\\]|\\.)*",?)*\})? -?[0-9.e+-]+$')


def span(kind, start, dur, name="x", status="ok", error="", trace="t1", **attrs):
    return Span(trace_id=trace, span_id=f"{kind}{start}{name}", parent_id=None, name=name, kind=kind, start=start, duration_s=dur,
                status=status, error=error, attrs=attrs)


def agent_spans(base=None, trace="t1"):
    t = base if base is not None else time.time() - 60
    return [span("run", t, 10, "invoke_agent", trace=trace, solved=True),
            span("llm", t, 4, "chat", trace=trace, provider="llamacpp", model="qwen3.5-9b", tokens_in=100, tokens_out=20, cost_usd=0.01),
            span("tool", t + 5, 1, "read_file", trace=trace),
            span("tool", t + 6, 1, "eda", "error", "tool_error", trace=trace),
            span("tool", t + 7, 1, "eda", trace=trace),
            span("tool", t + 8, 0.1, "write_file", "error", "denied", trace=trace)]


# ---------------------------------------------------------------------------------------------------- store

def test_spans_roundtrip_and_filter_by_time(tmp_path):
    store = Store(tmp_path / "s.db")
    old, new = agent_spans(base=1000.0, trace="old"), agent_spans(base=2000.0, trace="new")
    for s in old + new:
        store.add_span(s, session="abc")
    store.add_span(new[0], session="abc")  # the same span written twice is one row, not two
    got = store.spans_since(1500.0)
    assert len(got) == len(new) and {s.trace_id for s in got} == {"new"}
    llm = next(s for s in got if s.kind == "llm")
    assert llm.attrs["tokens_in"] == 100 and llm.duration_s == 4 and [s.start for s in got] == sorted(s.start for s in got)


def test_run_counts(tmp_path):
    store = Store(tmp_path / "s.db")
    store.create_run("r1", "spec", "replay", "simulated", {})
    store.create_run("r2", "spec", "replay", "simulated", {})
    store.finish_run("r1", "succeeded", {"signoff_passed": True}, {"total_cost_usd": 0.5, "total_tokens": 100}, 3.0, None)
    counts = store.run_counts()
    assert counts["by_status"] == {"queued": 1, "succeeded": 1}
    assert (counts["cost_usd"], counts["tokens"], counts["duration_s_sum"], counts["duration_n"], counts["signoff_passed"]) == (
        0.5, 100, 3.0, 1, 1)  # the unfinished run has no duration, so it is not counted in the duration summary


# ---------------------------------------------------------------------------------------------- prometheus

def test_every_line_is_valid_exposition_format_and_histogram_is_cumulative():
    text = prometheus.render({"by_status": {"succeeded": 3, "failed": 1}, "cost_usd": 1.25, "tokens": 900, "duration_s_sum": 12.5,
                              "duration_n": 4, "signoff_passed": 2}, agent_spans())
    assert text.endswith("\n")
    for line in text.strip().splitlines():
        assert line.startswith("#") or SAMPLE.match(line), line
    assert 'siliconforge_runs{status="succeeded"} 3' in text
    assert 'siliconforge_agent_tool_calls_total{tool="eda",outcome="failed"} 1' in text
    assert 'siliconforge_agent_tool_calls_total{tool="eda",outcome="ok"} 1' in text
    assert 'siliconforge_agent_tool_calls_total{tool="write_file",outcome="refused"} 1' in text  # refused, not failed
    assert 'siliconforge_agent_llm_calls_total{provider="llamacpp",model="qwen3.5-9b"} 1' in text
    assert 'siliconforge_agent_tokens_total{direction="input"} 100' in text
    buckets = [int(float(m.group(2))) for m in re.finditer(r'siliconforge_agent_run_seconds_bucket\{le="([^"]+)"\} (\S+)', text)]
    assert buckets == sorted(buckets) and buckets[-1] == 1 and buckets[0] == 0  # one 10 s run: 0 in the 0.5 bucket, 1 from 10 s up
    assert "siliconforge_agent_run_seconds_count 1" in text
    assert 'siliconforge_agent_recovery_total{outcome="recovered"} 1' in text


def test_label_values_are_escaped():
    s = [span("llm", 0, 1, "chat", provider='we"ird', model="a\nb\\c", tokens_in=1, tokens_out=1)]
    text = prometheus.render({}, s)
    line = next(ln for ln in text.splitlines() if ln.startswith("siliconforge_agent_llm_calls_total"))
    assert SAMPLE.match(line) and r'\"' in line and r"\n" in line and "\n" not in line


def test_no_spans_still_renders_a_valid_page():
    text = prometheus.render({}, [])
    for line in text.strip().splitlines():
        assert line.startswith("#") or SAMPLE.match(line), line
    assert "siliconforge_agent_run_seconds_count 0" in text


# ---------------------------------------------------------------------------------------------- endpoints

@pytest.fixture
def client(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from services.orchestrator.api.app import create_app

    db = tmp_path / "api.db"
    app = create_app(db_path=db, serve_ui=False, runs_root=tmp_path / "runs")
    return TestClient(app), Store(db)  # a second connection to the same WAL file plays the part of an agent session


def test_metrics_and_insights_endpoints(client):
    c, store = client
    empty = c.get("/api/insights/agent").json()
    assert empty["spans"] == 0 and empty["metrics"] == {"runs": 0}
    for s in agent_spans():
        store.add_span(s, session="s1")
    r = c.get("/api/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert 'siliconforge_agent_tool_calls_total{tool="read_file",outcome="ok"} 1' in r.text
    ins = c.get("/api/insights/agent", params={"hours": 1}).json()
    m = ins["metrics"]
    assert ins["spans"] == 6 and m["runs"] == 1 and m["solved"] == 1
    assert m["tools"]["success_rate"] == pytest.approx(2 / 3, abs=1e-3) and m["tools"]["policy_blocks"] == 1
    assert m["recovery"]["rate"] == 1.0
    assert c.get("/api/insights/agent", params={"hours": 0}).status_code == 422


def test_spans_outside_the_window_are_not_counted(client):
    c, store = client
    for s in agent_spans(base=time.time() - 7200, trace="old"):
        store.add_span(s)
    assert c.get("/api/insights/agent", params={"hours": 1}).json()["spans"] == 0
    assert c.get("/api/insights/agent", params={"hours": 3}).json()["spans"] == 6


def test_the_token_protects_the_metrics_endpoint(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from services.orchestrator.api.app import create_app

    monkeypatch.setenv("SILICONFORGE_API_TOKEN", "sekret")
    c = TestClient(create_app(db_path=tmp_path / "t.db", serve_ui=False, runs_root=tmp_path / "runs"))
    assert c.get("/api/metrics").status_code == 401
    assert c.get("/api/metrics", headers={"Authorization": "Bearer sekret"}).status_code == 200
