# SPDX-License-Identifier: AGPL-3.0-or-later
"""The static demo's data: recorded from real code, free of local details, and in step with the committed files.

Nothing here calls a model. The trace recorder drives the real agent with a scripted model and the real static lint engine, so
those tests skip when the engine is not built (python scripts/dev.py build-cpp).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.orchestrator.config import load_flow_config
from services.orchestrator.demo import bundle
from services.orchestrator.demo.record_trace import record

ROOT = Path(__file__).resolve().parents[1]
needs_engine = pytest.mark.skipif(not load_flow_config().telemetry_binary.exists(),
                                  reason="the C++ telemetry binary is not built (python scripts/dev.py build-cpp)")


# ------------------------------------------------------------------------------------------------ sanitising

@pytest.mark.parametrize("raw,clean", [
    (r"C:\PROFESSIONAL\Development\AI-HPC\siliconforge\runs\x\a.gds", r"runs\x\a.gds"),
    ("C:\\PROFESSIONAL\\Development\\siliconforge\\runs\\x", "runs\\x"),
    ("/mnt/c/PROFESSIONAL/Development/AI-HPC/siliconforge/runs/x/a.gds", "runs/x/a.gds"),
    ("area=1166um\ufffd", "area=1166um²"),
])
def test_scrub_removes_the_checkout_prefix(raw, clean):
    assert bundle.scrub_text(raw) == clean
    assert bundle.leftovers(bundle.scrub_text(raw)) == []


def test_leftovers_flags_what_the_scrubber_misses():
    assert bundle.leftovers(r"D:\other\place") and bundle.leftovers("/home/someone/x") and bundle.leftovers("/Users/a/b")
    assert bundle.leftovers("Findings: no errors, v: 3, class: A") == []  # a colon after a letter is not a drive


def test_committed_demo_runs_carry_no_local_details():
    files = sorted(bundle.DEMO_RUNS.glob("*.json"))
    assert files, "no recorded runs under benchmarks/demo/recorded/"
    for f in files:
        assert bundle.leftovers(f.read_text(encoding="utf-8")) == [], f.name
        report = json.loads(f.read_text(encoding="utf-8"))["report"]
        assert report["eda_mode"] != "simulated", "the demo shows runs made with real tools"
        assert report["state"]["signoff_passed"] is True
        assert "gds_path" not in report["state"]


# ------------------------------------------------------------------------------------------------ the trace

@needs_engine
def test_trace_is_real_tools_with_a_scripted_model_and_two_outcomes():
    t = record()
    assert "no language model was called" in t["provenance"]["model"]
    assert t["provenance"]["scripted"] == ["the model's replies"]
    before, ok, rev = t["events_before"], t["approve"], t["revise"]
    assert before[0]["kind"] == "user" and t["checkpoint"]["tool"] == "write_file"
    assert ok[0]["kind"] == "approval_request" and ok[0]["decision"] == "approved"
    assert rev[0]["kind"] == "approval_request" and rev[0]["decision"] == "declined"
    # the linter's verdict comes from the real engine, and both branches end clean
    lint = [json.loads(e["text"]) for e in ok if e["kind"] == "tool_result" and e["tool"] == "lint_rtl"]
    assert lint[0]["passed"] is False and {f["rule"] for f in lint[0]["findings"]} == {"SF-RTL-002", "SF-RTL-003"}
    assert lint[-1]["passed"] is True
    assert ok[-1]["kind"] == "done" and rev[-1]["kind"] == "done"
    # a decline really stops the turn, so the revision is a second user message
    assert any(e["kind"] == "user" for e in rev)
    # transcript compaction ran, not just described
    assert {e.get("context") for e in ok + rev if e["kind"] == "tool_call"} & {"summaries", "digest", "digest+shrunk"}


@needs_engine
def test_trace_is_deterministic():
    assert record() == record()


# ------------------------------------------------------------------------------------------------ the bundle

def _read(root: Path) -> dict[str, object]:
    out: dict[str, object] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            out[rel] = json.loads(p.read_text(encoding="utf-8")) if p.suffix == ".json" else p.read_bytes()
    return out


@needs_engine
def test_committed_bundle_is_current(tmp_path):
    """Fails when results, runs, the recorder or the API shapes changed without `python scripts/export_demo_bundle.py`."""
    bundle.build(tmp_path / "data", tmp_path / "assets")
    assert _read(bundle.DEFAULT_OUT) == _read(tmp_path / "data")
    assert _read(bundle.DEFAULT_ASSETS) == _read(tmp_path / "assets")


def test_bundle_leaks_nothing_about_the_machine_that_built_it():
    texts = {p.name: p.read_text(encoding="utf-8") for p in bundle.DEFAULT_OUT.rglob("*.json")}
    assert texts, "the demo bundle is missing: run python scripts/export_demo_bundle.py"
    for name, text in texts.items():
        assert bundle.leftovers(text) == [], name
    assert json.loads(texts["doctor.json"])["gpu"] == {}
    assert json.loads(texts["models.json"])["models"] == []


def test_bundle_serves_only_committed_results():
    manifest = json.loads((bundle.DEFAULT_OUT / "manifest.json").read_text(encoding="utf-8"))
    committed = sorted(p.name for p in (ROOT / "benchmarks" / "results").glob("*.json"))
    assert manifest["benchmark_files"] == committed


def test_demo_assets_stay_within_budget():
    sizes = [p.stat().st_size for p in bundle.DEFAULT_ASSETS.glob("*")]
    assert sizes and max(sizes) <= bundle.MAX_ASSET_BYTES and sum(sizes) <= 6_000_000


def test_engineering_note_is_a_file_the_author_writes():
    """The note is the author's own writing; this repo's tooling only checks that it exists and is not auto-generated."""
    note = (ROOT / "services" / "dashboard" / "src" / "demo" / "engineeringNote.md").read_text(encoding="utf-8")
    assert note.lstrip().startswith("# ")
