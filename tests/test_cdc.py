# SPDX-License-Identifier: AGPL-3.0-or-later
"""The structural CDC check, on crafted crossings: good and bad, one signal and a bus, and what it says it cannot see."""
from __future__ import annotations

from services.orchestrator.main import cli_main
from services.orchestrator.verify.cdc import analyze

HEADER = """module m (input logic clk_a, input logic clk_b, input logic rst_n, input logic d, output logic q);
  logic flag_a;
  logic [7:0] bus_a;
  logic s1, s2;
"""
SRC_A = """
  always_ff @(posedge clk_a or negedge rst_n) begin
    if (!rst_n) begin flag_a <= 1'b0; bus_a <= 8'd0; end
    else begin flag_a <= d; bus_a <= bus_a + 8'd1; end
  end
"""


def run(dst_block: str):
    return analyze(HEADER + SRC_A + dst_block + "endmodule\n")


def test_a_two_flop_synchronizer_is_recognised():
    rep = run("""
  always_ff @(posedge clk_b) begin
    s1 <= flag_a;
    s2 <= s1;
    q  <= s2;
  end
""")
    [c] = rep.crossings
    assert (c.signal, c.from_clock, c.to_clock, c.status) == ("flag_a", "clk_a", "clk_b", "synchronized")
    assert not rep.problems and rep.clocks == ["clk_a", "clk_b"]


def test_reading_another_domains_register_in_logic_is_unsynchronized():
    rep = run("""
  always_ff @(posedge clk_b) begin
    if (flag_a) q <= 1'b1; else q <= 1'b0;
  end
""")
    [c] = rep.crossings
    assert c.status == "unsynchronized" and "directly" in c.note
    assert rep.problems == [c]


def test_a_single_capture_flop_used_for_logic_is_flagged_as_one_stage():
    rep = run("""
  always_ff @(posedge clk_b) begin
    s1 <= flag_a;
    if (s1) q <= 1'b1; else q <= 1'b0;
  end
""")
    [c] = rep.crossings
    assert c.status == "single_stage" and "second flop" in c.note


def test_a_bus_through_a_synchronizer_synchronizes_but_carries_a_warning():
    rep = analyze("""module m (input logic clk_a, input logic clk_b, output logic [7:0] q);
  logic [7:0] bus_a, s1, s2;
  always_ff @(posedge clk_a) bus_a <= bus_a + 8'd1;
  always_ff @(posedge clk_b) begin s1 <= bus_a; s2 <= s1; q <= s2; end
endmodule""")
    [c] = rep.crossings
    assert c.status == "synchronized" and c.width == 8 and "Gray code" in c.note  # the skew risk is named, not hidden


def test_one_clock_means_nothing_can_cross_and_no_always_ff_is_reported_as_unparsed():
    one = analyze("module m(input logic clk, output logic q); always_ff @(posedge clk) q <= ~q; endmodule")
    assert one.crossings == [] and one.clocks == ["clk"]
    none = analyze("module m(input a, output y); assign y = a; endmodule")
    assert none.unparsed and none.crossings == []  # it says what it could not see


def test_comments_do_not_create_crossings_and_a_register_written_in_two_domains_is_reported():
    rep = analyze("""module m (input logic clk_a, input logic clk_b, output logic q);
  logic x, y;
  always_ff @(posedge clk_a) x <= ~x;
  // always_ff @(posedge clk_b) y <= x;   a commented-out read of x
  always_ff @(posedge clk_b) y <= ~y;
  always_ff @(posedge clk_a) y <= ~x;
endmodule""")
    assert rep.crossings == [] and any("y is written in two clock domains" in u for u in rep.unparsed)


def test_cli_exit_status_follows_the_findings(tmp_path, capsys):
    bad, good = tmp_path / "bad.sv", tmp_path / "good.sv"
    bad.write_text(HEADER + SRC_A + "always_ff @(posedge clk_b) if (flag_a) q <= 1; else q <= 0;\nendmodule\n", encoding="utf-8")
    good.write_text(HEADER + SRC_A + "always_ff @(posedge clk_b) begin s1 <= flag_a; s2 <= s1; q <= s2; end\nendmodule\n", encoding="utf-8")
    assert cli_main(["verify", "cdc", "--file", str(bad)]) == 1
    assert "unsynchronized" in capsys.readouterr().out
    assert cli_main(["verify", "cdc", "--file", str(good)]) == 0
    out = capsys.readouterr().out
    assert "heuristic" in out.lower()  # the report never presents itself as sign-off
