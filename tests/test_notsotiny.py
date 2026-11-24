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


def test_the_dataset_is_unreadable_while_scrambled_and_restored_exactly_afterwards(tmp_path: Path):
    f = ns.dataset_file(tmp_path)
    f.parent.mkdir(parents=True)
    original = b'{"id": "t1", "golden_module": "module m; endmodule"}\n' * 50
    f.write_bytes(original)
    with ns.scrambled_on_disk(tmp_path):
        during = f.read_bytes()
        assert len(during) == len(original) and b"golden_module" not in during and b"endmodule" not in during
    assert f.read_bytes() == original


def test_the_dataset_is_restored_even_if_the_run_raises(tmp_path: Path):
    f = ns.dataset_file(tmp_path)
    f.parent.mkdir(parents=True)
    f.write_bytes(b"module m; endmodule\n" * 20)
    before = f.read_bytes()
    with pytest.raises(RuntimeError), ns.scrambled_on_disk(tmp_path):
        raise RuntimeError("agent run blew up")
    assert f.read_bytes() == before


def test_the_audit_flags_a_lookup_of_the_reference_and_not_the_legitimate_check():
    from services.orchestrator.evals import plain_agent as pa

    ok = '{"CommandLine": "python C:\\repo\\scripts\\notsotiny.py check project.v"}'
    assert pa.audit(ok) == []
    assert pa.audit("open('C:/x/datasets/notsotiny/abc/tasks.jsonl')") == ["datasets/notsotiny", "tasks.jsonl"]
    assert "huggingface.co" in pa.audit("curl https://huggingface.co/datasets/HPAI-BSC/NotSoTiny-26-07")
    assert "golden_module" in pa.audit("print(row['golden_module'])")


def test_compare_and_table_report_the_second_arm_minus_the_first(tmp_path: Path, capsys):
    import json
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import notsotiny as cli

    def doc(arm, verdicts):
        rate = sum(v == "equivalent" for v in verdicts) / len(verdicts)
        return {"arm": arm, "model": arm, "tasks_run": len(verdicts), "equivalent_rate": {"mean": rate, "ci95": [rate, rate]}, "syntax_ok_rate": 1.0,
                "per_task": [{"id": f"t{i}", "verdict": v} for i, v in enumerate(verdicts)]}

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(doc("weak", ["x"] * 8 + ["equivalent"] * 2)), encoding="utf-8")
    b.write_text(json.dumps(doc("strong", ["equivalent"] * 6 + ["x"] * 4)), encoding="utf-8")
    cli.cmd_compare(type("A", (), {"a": str(a), "b": str(b)})())
    assert "+40.0 points" in capsys.readouterr().out
    cli.cmd_table(type("A", (), {"files": [str(a), str(b)]})())
    assert "+40.0 points" in capsys.readouterr().out
