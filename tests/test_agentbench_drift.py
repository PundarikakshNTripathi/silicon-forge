# SPDX-License-Identifier: AGPL-3.0-or-later
"""Comparing two runs of the same benchmark: a regression needs both a large enough drop and an interval that excludes zero."""
from __future__ import annotations

import json

import pytest

from services.orchestrator.agentbench import report
from services.orchestrator.main import cli_main

TASKS = [f"t{i}" for i in range(12)]


def metrics(tool=0.95, rec=0.8, p50=4.0, tok=5000.0):
    return {"tools": {"success_rate": tool}, "recovery": {"rate": rec}, "latency": {"e2e_p50_s": p50}, "cost": {"tokens_per_run": tok}}


def doc(scores: dict[str, dict[str, float]], model="m", versions=None, **m):
    return {"model": model, "versions": versions or {}, "summary": {"arms": {
        arm: {"by_task": by, "metrics": metrics(**m)} for arm, by in scores.items()}}}


def flat(v):
    return {t: v for t in TASKS}


def test_a_large_consistent_drop_is_a_regression():
    c = report.compare_docs(doc({"custom": flat(1.0)}), doc({"custom": flat(0.6)}))
    e = c["arms"]["custom"]
    assert e["task_success_change"] == pytest.approx(-0.4) and e["separated"] and e["regression"]
    assert c["regressions"] == ["custom"] and sorted(e["worse_tasks"]) == sorted(TASKS)


def test_a_noisy_drop_the_interval_cannot_prove_is_flagged_but_not_a_regression():
    before = {t: 1.0 for t in TASKS}
    after = dict(before)
    after["t0"] = 0.0  # one task of twelve lost: a drop of 8.3 points, below a 10-point threshold
    c = report.compare_docs(doc({"custom": before}), doc({"custom": after}), max_drop=0.05)
    e = c["arms"]["custom"]
    assert e["task_success_change"] == pytest.approx(-1 / 12)
    # one task out of twelve changing: resampling tasks often draws none of the changed ones, so the interval reaches zero
    assert not e["separated"], e["ci95"]
    assert e["drop_without_proof"] and not e["regression"] and c["regressions"] == []  # worth a look, not an alarm
    # the same drop on every task is a proven one
    c2 = report.compare_docs(doc({"custom": before}), doc({"custom": flat(0.9)}), max_drop=0.05)
    assert c2["arms"]["custom"]["regression"] and not c2["arms"]["custom"]["drop_without_proof"]


def test_improvement_and_no_change_are_not_regressions():
    assert report.compare_docs(doc({"a": flat(0.5)}), doc({"a": flat(0.9)}))["regressions"] == []
    same = report.compare_docs(doc({"a": flat(0.7)}), doc({"a": flat(0.7)}))
    assert same["regressions"] == [] and same["arms"]["a"]["task_success_change"] == 0 and same["arms"]["a"]["worse_tasks"] == []


def test_only_arms_and_tasks_present_in_both_are_compared():
    before = doc({"a": {"t1": 1.0, "t2": 1.0}, "gone": flat(1.0)})
    after = doc({"a": {"t2": 0.0, "t3": 1.0}, "new": flat(1.0)})
    c = report.compare_docs(before, after)
    assert list(c["arms"]) == ["a"] and c["arms"]["a"]["tasks"] == 1  # only t2 is in both


def test_metric_changes_and_version_changes_are_reported():
    c = report.compare_docs(doc({"a": flat(0.8)}, versions={"langgraph": "1.2.0", "openai": "3.0"}, tool=0.95, p50=4.0),
                            doc({"a": flat(0.8)}, versions={"langgraph": "1.3.0", "openai": "3.0"}, tool=0.90, p50=6.0))
    assert c["arms"]["a"]["metric_changes"]["tool_success_rate"] == pytest.approx(-0.05)
    assert c["arms"]["a"]["metric_changes"]["e2e_p50_s"] == pytest.approx(2.0)
    assert c["versions_changed"] == {"langgraph": ["1.2.0", "1.3.0"]}  # openai is unchanged, so it is not listed
    text = report.render_compare(c)
    assert "langgraph 1.2.0 -> 1.3.0" in text and "[a] task success" in text


def test_cli_exit_status_follows_the_gate(tmp_path, capsys):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(doc({"custom": flat(1.0)})), encoding="utf-8")
    bad.write_text(json.dumps(doc({"custom": flat(0.5)})), encoding="utf-8")
    assert cli_main(["agentbench", "compare", str(good), str(bad)]) == 0  # reported, but not a gate
    assert "REGRESSION" in capsys.readouterr().out
    assert cli_main(["agentbench", "compare", str(good), str(bad), "--gate"]) == 1
    assert cli_main(["agentbench", "compare", str(bad), str(good), "--gate"]) == 0  # getting better never fails the gate
    assert cli_main(["agentbench", "compare", str(good)]) == 2
    capsys.readouterr()
    assert cli_main(["agentbench", "compare", str(good), str(bad), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["regressions"] == ["custom"]
