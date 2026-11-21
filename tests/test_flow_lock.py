# SPDX-License-Identifier: AGPL-3.0-or-later
"""flow.lock: written from the installed tools, enforced by `flow run --locked`, and never satisfied by a missing tool."""
from __future__ import annotations

import argparse
import json

import pytest

from services.orchestrator.flow import cli as flow_cli
from services.orchestrator.flow import lock as flock
from services.orchestrator.project import init_project

TOOLS = {"verilator": "Verilator 5.055", "yosys": "Yosys 0.71", "librelane": "LibreLane 3.0.17"}


def test_make_lock_refuses_missing_tools():
    for bad in ("not found", "unknown", ""):
        with pytest.raises(flock.LockError, match="librelane"):
            flock.make_lock({**TOOLS, "librelane": bad}, "sky130A", "top")


def test_roundtrip_and_mismatches(tmp_path):
    lock = flock.make_lock(TOOLS, "sky130A", "top")
    flock.write_lock(tmp_path, lock)
    back = flock.read_lock(tmp_path)
    assert flock.mismatches(back, TOOLS, "sky130A") == []

    newer = {**TOOLS, "yosys": "Yosys 0.72"}
    assert flock.mismatches(back, newer, "sky130A") == ["yosys: locked 'Yosys 0.71', installed 'Yosys 0.72'"]
    assert flock.mismatches(back, {k: v for k, v in TOOLS.items() if k != "yosys"}, "sky130A") == [
        "yosys: locked 'Yosys 0.71', installed 'not found'"]  # a missing tool does not match
    assert "extra" in flock.mismatches(back, {**TOOLS, "extra": "1"}, "sky130A")[0]
    assert flock.mismatches(back, TOOLS, "gf180mcuD") == ["pdk: locked 'sky130A', project uses 'gf180mcuD'"]


def test_reading_a_bad_lock_is_an_error_not_a_pass(tmp_path):
    with pytest.raises(flock.LockError, match="no flow.lock"):
        flock.read_lock(tmp_path)
    (tmp_path / "flow.lock").write_text("{not json", encoding="utf-8")
    with pytest.raises(flock.LockError, match="not valid JSON"):
        flock.read_lock(tmp_path)
    (tmp_path / "flow.lock").write_text(json.dumps({"schema": "other", "tools": {}}), encoding="utf-8")
    with pytest.raises(flock.LockError, match="siliconforge.flow.lock/1"):
        flock.read_lock(tmp_path)


class _StubFlow:
    def __init__(self, tools):
        self.tools, self.runner, self.ran = tools, type("R", (), {"name": "stub"})(), False

    def tool_versions(self):
        return self.tools

    def run(self, *a, **k):
        self.ran = True
        return {"stages": [], "overall": "clean"}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "top.sv").write_text("module top(input logic clk); endmodule\n", encoding="utf-8")
    init_project(tmp_path, "top", ["top.sv"], "clk", 10.0, "sky130A")
    return tmp_path


def _install(monkeypatch, stub):
    def make(project, args, out, **kw):
        out.mkdir(parents=True, exist_ok=True)  # the real ProjectFlow creates its output folder
        return stub

    monkeypatch.setattr(flow_cli, "_flow", make)
    monkeypatch.setattr(flow_cli, "to_html", lambda r: "<html></html>")
    return stub


def _ns(project_dir, cmd, **kw):
    base = {"flow_cmd": cmd, "path": str(project_dir), "backend": "wsl", "check": False, "locked": False, "stages": "lint",
            "no_lec": False, "knob": [], "waive": [], "timeout_pnr": 1.0, "out": str(project_dir / "out")}
    return argparse.Namespace(**{**base, **kw})


def test_lock_command_writes_then_checks(project, monkeypatch, capsys):
    _install(monkeypatch, _StubFlow(TOOLS))
    assert flow_cli.cmd_flow(_ns(project, "lock")) == 0 and (project / "flow.lock").is_file()
    assert flow_cli.cmd_flow(_ns(project, "lock", check=True)) == 0
    assert "holds" in capsys.readouterr().out

    _install(monkeypatch, _StubFlow({**TOOLS, "yosys": "Yosys 0.72"}))
    assert flow_cli.cmd_flow(_ns(project, "lock", check=True)) == 3
    assert "yosys" in capsys.readouterr().out


def test_locked_run_refuses_on_drift_and_does_not_run(project, monkeypatch, capsys):
    flock.write_lock(project, flock.make_lock(TOOLS, "sky130A", "top"))
    drifted = {**TOOLS, "librelane": "LibreLane 3.1"}

    free = _install(monkeypatch, _StubFlow(drifted))
    assert flow_cli.cmd_flow(_ns(project, "run")) == 0 and free.ran  # without --locked the drift is not enforced

    blocked = _install(monkeypatch, _StubFlow(drifted))
    assert flow_cli.cmd_flow(_ns(project, "run", locked=True)) == 3 and not blocked.ran
    assert "librelane" in capsys.readouterr().err

    ok = _install(monkeypatch, _StubFlow(TOOLS))
    assert flow_cli.cmd_flow(_ns(project, "run", locked=True)) == 0 and ok.ran


def test_locked_run_without_a_lock_is_an_error(project, monkeypatch):
    stub = _install(monkeypatch, _StubFlow(TOOLS))
    assert flow_cli.cmd_flow(_ns(project, "run", locked=True)) == 2 and not stub.ran
