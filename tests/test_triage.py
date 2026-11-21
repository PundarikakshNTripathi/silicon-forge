# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression triage: values that change between seeds must not split a bucket; different faults must not merge; a crash is
never a pass."""
from __future__ import annotations

import json

import pytest

from services.orchestrator.main import cli_main as sf_main
from services.orchestrator.verify import triage as tri
from services.orchestrator.verify.tools.sf_lockstep import LockstepMismatch


def lockstep_msg(step, diffs):
    return str(LockstepMismatch(step, diffs, ["ADD r1, r2", "JMP 4"]))


def test_lockstep_divergence_is_keyed_by_the_fields_not_the_values():
    a = lockstep_msg(12, {"pc": (4, 5), "acc": (0, 0x10)})
    b = lockstep_msg(307, {"acc": (9, 3), "pc": (99, 100)})
    c = lockstep_msg(12, {"pc": (4, 5)})
    assert tri.signature(a) == tri.signature(b) == "lockstep divergence on acc, pc"
    assert tri.signature(c) == "lockstep divergence on pc"


def test_generic_messages_lose_numbers_hex_bits_and_quotes():
    s1 = tri.signature("AssertionError: uart rx byte 0x5a != 0xa5 at 1200 ns on 'tb.dut.rx'")
    s2 = tri.signature("AssertionError: uart rx byte 0x00 != 0xff at 87 ns on 'tb.other'")
    assert s1 == s2
    assert tri.signature("mismatch 4'b1010 vs 4'b0101") == tri.signature("mismatch 4'b0000 vs 4'b1111")
    assert tri.signature("timeout waiting for ready after 5000 cycles") == tri.signature("timeout waiting for ready after 12 cycles")
    # different faults stay apart
    assert tri.signature("timeout waiting for ready") != tri.signature("timeout waiting for valid")
    assert tri.signature("") == "(failure with no message)"


def write(tmp_path, name, runs):
    p = tmp_path / name
    p.write_text(json.dumps({"runs": runs}), encoding="utf-8")
    return p


def test_buckets_count_seeds_and_sort_by_size(tmp_path):
    div = lambda step: lockstep_msg(step, {"pc": (1, 2)})  # noqa: E731
    p1 = write(tmp_path, "a.json", [
        {"seed": 1, "tests": [{"name": "t.run", "status": "fail", "message": div(3)}], "crashed": ""},
        {"seed": 2, "tests": [{"name": "t.run", "status": "pass", "message": ""}], "crashed": ""},
        {"seed": 3, "tests": [{"name": "t.run", "status": "fail", "message": div(40)},
                              {"name": "t.other", "status": "fail", "message": "timeout after 9 cycles"}], "crashed": ""},
    ])
    p2 = write(tmp_path, "b.json", [{"seed": 4, "tests": [{"name": "t.run", "status": "fail", "message": div(8)}], "crashed": ""}])
    res = tri.triage([p1, p2])
    assert (res["ran"], res["passed"], res["failed"]) == (5, 1, 4)
    top = res["buckets"][0]
    assert top["signature"] == "lockstep divergence on pc" and top["failures"] == 3 and top["seeds"] == [1, 3, 4]
    assert top["sources"] == ["a.json", "b.json"]
    assert res["buckets"][1]["signature"] == "timeout after N cycles" and res["buckets"][1]["tests"] == ["t.other"]


def test_a_crash_with_no_results_is_a_failure_in_its_own_bucket(tmp_path):
    p = write(tmp_path, "c.json", [{"seed": 7, "tests": [], "crashed": "RuntimeError: simulator exited with 1"}])
    res = tri.triage([p])
    assert res["failed"] == 1 and res["buckets"][0]["signature"].startswith("crash: RuntimeError")


def test_skips_are_not_counted_and_a_report_that_never_ran_is_an_error(tmp_path):
    p = write(tmp_path, "d.json", [{"seed": 1, "tests": [{"name": "t.x", "status": "skip", "message": ""}], "crashed": ""}])
    assert tri.triage([p])["ran"] == 0
    bad = tmp_path / "e.json"
    bad.write_text(json.dumps({"error": "cocotb runner not available"}), encoding="utf-8")
    with pytest.raises(ValueError, match="did not run"):
        tri.triage([bad])


def test_render_shows_the_replay_command_for_the_lowest_seed(tmp_path):
    p = write(tmp_path, "f.json", [{"seed": 9, "tests": [{"name": "t.x", "status": "fail", "message": "boom 1"}], "crashed": ""},
                                   {"seed": 5, "tests": [{"name": "t.x", "status": "fail", "message": "boom 2"}], "crashed": ""}])
    text = tri.render(tri.triage([p]), "siliconforge verify sim --seeds {seed}")
    assert "2x  boom N" in text and "replay: siliconforge verify sim --seeds 5" in text


def test_cli_exit_codes(tmp_path, capsys):
    ok = write(tmp_path, "ok.json", [{"seed": 1, "tests": [{"name": "t", "status": "pass", "message": ""}], "crashed": ""}])
    bad = write(tmp_path, "bad.json", [{"seed": 1, "tests": [{"name": "t", "status": "fail", "message": "x"}], "crashed": ""}])
    assert sf_main(["triage", str(ok)]) == 0
    capsys.readouterr()
    assert sf_main(["triage", str(bad), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["buckets"][0]["signature"] == "x"
    assert sf_main(["triage", str(tmp_path / "missing.json")]) == 2


# -------------------------------------------------------------------------------- the runner's XML parsing

def test_parse_results_reads_cocotb_2_error_msg(tmp_path):
    """cocotb 2.x puts the failure text in `error_msg`, not `message`; reading only `message` left sim.json blank."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "services" / "orchestrator" / "verify" / "tools" / "sf_cocotb_run.py"
    spec = importlib.util.spec_from_file_location("sf_cocotb_run_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    xml = tmp_path / "results.xml"
    xml.write_text(
        '<testsuites><testsuite>'
        '<testcase name="t_bad" classname="m" time="0.1" sim_time_ns="5.0">'
        '<failure error_type="LockstepMismatch" error_msg="RTL and model diverge after instruction #3:&#10;  x: rtl=1 model=2"/>'
        '</testcase>'
        '<testcase name="t_junit" classname="m"><failure message="plain junit" type="AssertionError"/></testcase>'
        '<testcase name="t_ok" classname="m"/>'
        '</testsuite></testsuites>', encoding="utf-8")
    bad, junit, ok = mod.parse_results(xml)
    assert bad["status"] == "fail" and "diverge after instruction #3" in bad["message"] and bad["error_type"] == "LockstepMismatch"
    assert junit["message"] == "plain junit" and junit["error_type"] == "AssertionError"
    assert ok["status"] == "pass" and ok["message"] == ""
    # and the triage of that report keys on the field, as intended
    out = tmp_path / "sim.json"
    out.write_text(json.dumps({"runs": [{"seed": 1, "tests": [bad], "crashed": ""}]}), encoding="utf-8")
    assert tri.triage([out])["buckets"][0]["signature"] == "lockstep divergence on x"
