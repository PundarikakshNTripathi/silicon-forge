# SPDX-License-Identifier: AGPL-3.0-or-later
"""The agent's read-only debug tools: cone_of, waveform, trace_diff."""
import pytest

from services.orchestrator.agent.tools import READ, ToolBox, ToolError
from services.orchestrator.workspace import init_workspace

RTL = """module m(input clk, input en, input [3:0] d, output reg [3:0] q, output other);
  assign other = ~en;
  always @(posedge clk) if (en) q <= d;
endmodule
"""

VCD_A = """$timescale 1ns $end
$scope module top $end
$var wire 1 ! clk $end
$var wire 4 " q [3:0] $end
$upscope $end
$enddefinitions $end
#0
0!
b0000 "
#10
1!
b0001 "
#20
b0010 "
"""
VCD_B = VCD_A.replace("#20\nb0010", "#20\nb0011")


@pytest.fixture
def tb(tmp_path):
    ws = init_workspace(tmp_path / "proj", profile="dev", backend="simulated")
    (ws.root / "dut.sv").write_text(RTL, encoding="utf-8")
    (ws.root / "a.vcd").write_text(VCD_A, encoding="utf-8")
    (ws.root / "b.vcd").write_text(VCD_B, encoding="utf-8")
    return ToolBox(ws)


def test_the_debug_tools_are_read_class_so_plan_mode_can_use_them(tb):
    specs = tb.by_name()
    assert all(specs[n].permission == READ for n in ("cone_of", "waveform", "trace_diff"))


def test_cone_of_includes_the_enable_and_excludes_unrelated_logic(tb):
    r = tb.call("cone_of", {"path": "dut.sv", "signal": "q", "context_lines": 0})
    assert r["found"] and {"en", "d", "clk"} <= set(r["signals"]) and "other" not in r["signals"]
    assert "assign other" not in r["slice"] and "q <= d" in r["slice"]


def test_cone_of_an_input_says_so_instead_of_guessing(tb):
    r = tb.call("cone_of", {"path": "dut.sv", "signal": "en"})
    assert r["found"] is False and "never assigned" in r["note"]


def test_waveform_returns_a_window_and_the_signal_list(tb):
    r = tb.call("waveform", {"path": "a.vcd", "time": 20, "mark": "q"})
    assert r["signals"] == ["clk", "q"] and ">q" in r["window"] and "@20" in r["window"]


def test_trace_diff_reports_the_earliest_difference(tb):
    r = tb.call("trace_diff", {"path_a": "a.vcd", "path_b": "b.vcd"})
    assert r["identical"] is False and r["time"] == 20 and r["differing_signals"] == ["q"]
    assert tb.call("trace_diff", {"path_a": "a.vcd", "path_b": "a.vcd"})["identical"] is True


def test_waveform_refuses_paths_outside_the_workspace_and_fst_files(tb, tmp_path):
    (tb.ws.root / "x.fst").write_bytes(b"\x00")
    with pytest.raises(ToolError, match="FST"):
        tb.call("waveform", {"path": "x.fst"})
    with pytest.raises(ToolError):
        tb.call("waveform", {"path": "../escape.vcd"})
