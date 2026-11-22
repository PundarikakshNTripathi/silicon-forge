# SPDX-License-Identifier: AGPL-3.0-or-later
"""The evals regression gate, and static checks that the container files keep their hardening (the build itself runs in the Docker workflow)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from services.orchestrator.main import cli_main

ROOT = Path(__file__).resolve().parents[1]


def result(path: Path, passes: list[int]) -> Path:
    problems = [{"id": f"p{i}", "n": 1, "c": c} for i, c in enumerate(passes)]
    doc = {"schema": "siliconforge.eval/1", "model": "m", "arm": {"arm": "single"}, "pass_at_1": {"k": 1, "mean": sum(passes) / len(passes)},
           "per_problem": problems}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_gate_fails_only_on_a_proven_drop(tmp_path, capsys):
    base = result(tmp_path / "base.json", [1] * 40)
    worse = result(tmp_path / "worse.json", [1] * 20 + [0] * 20)       # a large, consistent drop
    same = result(tmp_path / "same.json", [1] * 40)
    assert cli_main(["evals", "compare", str(base), str(worse), "--gate", "--max-drop", "0.08"]) == 1
    assert "REGRESSION" in capsys.readouterr().out
    assert cli_main(["evals", "compare", str(base), str(same), "--gate"]) == 0
    assert cli_main(["evals", "compare", str(worse), str(base), "--gate"]) == 0   # getting better never fails the gate
    assert cli_main(["evals", "compare", str(base), str(worse)]) == 0             # without --gate it only reports


def test_a_small_noisy_drop_is_reported_but_not_a_regression(tmp_path, capsys):
    base = result(tmp_path / "b.json", [1] * 12)
    noisy = result(tmp_path / "n.json", [1] * 11 + [0])                         # one problem of twelve: 8.3 points, interval includes 0
    assert cli_main(["evals", "compare", str(base), str(noisy), "--gate", "--max-drop", "0.05"]) == 0
    assert "not proven" in capsys.readouterr().out


def test_orchestrator_image_is_unprivileged_has_a_healthcheck_and_pinned_bases():
    text = (ROOT / "docker" / "Dockerfile.orchestrator").read_text(encoding="utf-8")
    assert re.search(r"^USER sf$", text, re.M) and "--uid 10001" in text
    assert "HEALTHCHECK" in text and "/api/health" in text
    assert "--frozen-lockfile" in text
    for image in re.findall(r"^FROM (\S+)", text, re.M):
        assert ":" in image.split(" ")[0] and not image.endswith(":latest"), image


def test_compose_publishes_on_loopback_and_drops_privileges():
    doc = yaml.safe_load((ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "version" not in doc  # the obsolete top-level key
    orch = doc["services"]["orchestrator"]
    assert all(p.startswith("127.0.0.1:") for p in orch["ports"])
    assert orch["cap_drop"] == ["ALL"] and "no-new-privileges:true" in orch["security_opt"]
    assert "SILICONFORGE_API_TOKEN" in orch["environment"]


def test_dockerignore_keeps_secrets_weights_and_history_out_of_the_image():
    lines = set((ROOT / ".dockerignore").read_text(encoding="utf-8").split())
    assert {".git", ".env", "*.gguf", "models", "runs"} <= lines


def test_canary_never_runs_on_its_own_and_does_not_put_inputs_in_shell_text():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "canary.yml").read_text(encoding="utf-8"))
    triggers = doc.get(True) or doc.get("on")  # YAML 1.1 reads a bare `on` as True
    assert set(triggers) == {"workflow_dispatch"}  # it spends the user's tokens: manual unless they uncomment a schedule
    for step in doc["jobs"]["canary"]["steps"]:
        assert "${{" not in step.get("run", ""), step.get("name")
