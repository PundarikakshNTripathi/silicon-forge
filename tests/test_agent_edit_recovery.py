# SPDX-License-Identifier: AGPL-3.0-or-later
"""edit_file recovery and the prompt rules added after the AgentBench run: 11 of the loop's 34 edit_file calls had missed their target
(wrong indentation or a near-miss), and it ignored a requested answer format. These tests fail without the changes."""
from __future__ import annotations

import pytest

from services.orchestrator.agent.session import SYSTEM
from services.orchestrator.agent.tools import ToolBox, ToolError
from services.orchestrator.workspace import init_workspace

SRC = """module cnt (
  input  logic clk,
  output logic [3:0] q
);
  always_ff @(posedge clk) begin
    q <= q + 4'd1;
  end
endmodule
"""


@pytest.fixture
def box(tmp_path):
    ws = init_workspace(tmp_path / "p", profile="dev", backend="simulated")
    (ws.root / "cnt.sv").write_text(SRC, encoding="utf-8")
    return ToolBox(ws), ws


def test_an_exact_edit_still_works(box):
    tb, ws = box
    out = tb.edit_file("cnt.sv", "q <= q + 4'd1;", "q <= q + 4'd2;")
    assert out == {"path": "cnt.sv", "replaced": 1}
    assert "4'd2" in (ws.root / "cnt.sv").read_text(encoding="utf-8")


def test_wrong_indentation_is_applied_when_it_is_unambiguous_and_says_so(box):
    tb, ws = box
    out = tb.edit_file("cnt.sv", "always_ff @(posedge clk) begin\nq <= q + 4'd1;\nend", "always_ff @(posedge clk) begin\n    q <= q + 4'd3;\n  end")
    assert out["replaced"] == 1 and "whitespace" in out["note"]
    text = (ws.root / "cnt.sv").read_text(encoding="utf-8")
    assert "4'd3" in text and "4'd1" not in text and text.endswith("endmodule\n")  # replaced in place, the rest untouched


def test_a_near_miss_names_the_closest_text_with_line_numbers(box):
    tb, _ = box
    with pytest.raises(ToolError) as err:
        tb.edit_file("cnt.sv", "q <= q + 4'd10;", "q <= 0;")
    msg = str(err.value)
    assert "not found" in msg and "Closest text" in msg and "lines 6-" in msg and "q <= q + 4'd1;" in msg


def test_an_ambiguous_match_is_not_guessed_and_asks_for_more_context(tmp_path):
    ws = init_workspace(tmp_path / "p", profile="dev", backend="simulated")
    (ws.root / "a.sv").write_text("x <= 1;\nx <= 1;\n", encoding="utf-8")
    with pytest.raises(ToolError, match="matched 2 times.*more of the surrounding lines"):
        ToolBox(ws).edit_file("a.sv", "x <= 1;", "x <= 2;")
    with pytest.raises(ToolError):  # also when the only match would be a whitespace-insensitive tie
        ToolBox(ws).edit_file("a.sv", "  x <= 1;  ", "x <= 2;")
    assert (ws.root / "a.sv").read_text(encoding="utf-8") == "x <= 1;\nx <= 1;\n"


def test_the_prompt_asks_for_the_requested_format_and_for_a_check_after_every_change():
    assert "ANSWER: <value>" in SYSTEM and "put exactly that format in the `say`" in SYSTEM
    assert "check the change before you report success" in SYSTEM and "search the whole folder for the old name" in SYSTEM
