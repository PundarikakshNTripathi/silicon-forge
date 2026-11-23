# SPDX-License-Identifier: AGPL-3.0-or-later
"""NotSoTiny plumbing: splicing a candidate into a project, extracting code from a reply, and grading with real Yosys. No network, no model."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.orchestrator.evals import notsotiny as ns

GOLDEN = """module adder (
  input  wire [3:0] a,
  input  wire [3:0] b,
  output wire [4:0] y
);
  assign y = a + b;
endmodule
"""
PROMPT = f"""`line 1 "top.v" 1
module top (input wire [3:0] a, input wire [3:0] b, output wire [4:0] y);
  adder u (.a(a), .b(b), .y(y));
endmodule
`line 1 "adder.v" 1
module adder (
  input  wire [3:0] a,
  input  wire [3:0] b,
  output wire [4:0] y
);
{ns.BEGIN}

{ns.END}
endmodule
"""


def task() -> ns.Task:
    return ns.Task("t1", "p", "task_adder", "system", PROMPT, GOLDEN)


def test_a_task_with_one_region_and_a_findable_header_is_usable():
    assert ns.usable(task()) is None
    assert ns.usable(ns.Task("t", "p", "n", "s", "module x; endmodule", GOLDEN)) == "no single masked region in the prompt"
    assert ns.usable(ns.Task("t", "p", "n", "s", PROMPT, GOLDEN.replace("adder", "other"))) == "the reference header is not found in the project"


def test_extract_body_takes_the_fenced_block_and_drops_repeated_markers():
    assert ns.extract_body("Here:\n```verilog\nassign y = a + b;\n```\nDone") == "assign y = a + b;"
    assert ns.extract_body(f"{ns.BEGIN}\nassign y = a + b;\n{ns.END}") .strip() == "assign y = a + b;"
    assert ns.extract_body("assign y = a;") == "assign y = a;"


def test_a_body_is_spliced_between_the_markers_and_a_whole_module_replaces_the_module():
    body_project = ns.candidate_project(task(), "assign y = a + b;")
    assert "assign y = a + b;" in body_project and body_project.count("module adder") == 1 and "`line" not in body_project
    whole = ns.candidate_project(task(), GOLDEN.replace("a + b", "a - b"))
    assert "a - b" in whole and whole.count("module adder") == 1 and whole.count("endmodule") == 2
    assert ns.golden_project(task()).count("assign y = a + b;") == 1
    assert ns.top_module(whole) == "top"


@pytest.mark.real_eda
def test_real_yosys_grades_a_right_a_wrong_and_a_broken_candidate(tmp_path: Path):
    from tests.test_equiv_grader import _real_runner

    runner = _real_runner()
    right = ns.grade(runner, tmp_path / "a", task(), "assign y = b + a;")
    assert right["verdict"] == "equivalent", right
    wrong = ns.grade(runner, tmp_path / "b", task(), "assign y = a - b;")
    assert wrong["verdict"] == "not_proven" and wrong["syntax"] is True, wrong
    broken = ns.grade(runner, tmp_path / "c", task(), "assign y = ;")
    assert broken["verdict"] == "syntax_error" and not broken["syntax"], broken
    kept, dropped = ns.selfcheck(runner, tmp_path / "d", [task()], want=1, timeout=120.0)
    assert [t.id for t in kept] == ["t1"] and not dropped
