# SPDX-License-Identifier: AGPL-3.0-or-later
"""The composite GitHub Action is valid, passes its inputs through the environment (not into the shell text), and always uploads the report."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ACTION = Path(__file__).resolve().parents[1] / ".github" / "actions" / "flow" / "action.yml"


def load():
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def test_action_is_a_valid_composite_with_documented_inputs():
    doc = load()
    assert doc["runs"]["using"] == "composite"
    assert {"path", "backend", "version", "extra-args", "locked"} <= set(doc["inputs"])
    assert all("description" in v for v in doc["inputs"].values())


def test_inputs_never_appear_inside_shell_commands():
    """`${{ inputs.x }}` inside a `run:` is script injection; the action routes every input through `env:`."""
    for step in load()["runs"]["steps"]:
        if "run" in step:
            assert "${{" not in step["run"], step.get("name")
            assert step.get("shell") == "bash"


def test_the_flow_runs_locked_by_default_and_the_report_is_uploaded_even_when_a_gate_fails():
    steps = load()["runs"]["steps"]
    run = next(s for s in steps if s.get("name") == "Run the flow")
    assert "--locked" in run["run"] and load()["inputs"]["locked"]["default"] == "true"
    upload = steps[-1]
    assert upload["uses"].startswith("actions/upload-artifact@") and upload["if"] == "always()"
    assert re.search(r"report\.html", upload["with"]["path"]) and re.search(r"report\.json", upload["with"]["path"])
