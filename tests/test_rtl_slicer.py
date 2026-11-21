# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cone-of-influence slicer: control dependencies, naming, multi-line statements, instances, literals."""
from services.orchestrator.guardrails.rtl_slicer import RtlDependencySlicer

S = RtlDependencySlicer()


def cone(rtl: str, target: str, module: str | None = None) -> set[str]:
    return S.cone(rtl, target, module).signals


def test_identifiers_starting_with_d_are_signals():
    # the old filter dropped every identifier starting with "d" (meant for decimal literals like 4'd5)
    rtl = "module m; assign done = data & dly; assign data = in_a ^ in_b; endmodule"
    assert {"data", "dly", "in_a", "in_b"} <= cone(rtl, "done")


def test_if_condition_is_a_dependency_of_the_register_it_gates():
    rtl = """module m(input clk, input en, input [3:0] d, output reg [3:0] q);
  always @(posedge clk)
    if (en) q <= d;
endmodule"""
    assert {"en", "d", "clk"} <= cone(rtl, "q")


def test_else_branch_also_depends_on_the_condition():
    rtl = """module m;
  always @(posedge clk) begin
    if (sel) y <= a;
    else y <= b;
  end
endmodule"""
    assert {"sel", "a", "b", "clk"} <= cone(rtl, "y")


def test_case_selector_and_all_arms_are_in_the_cone():
    rtl = """module m;
  always @* begin
    case (sel)
      2'b00: y = a;
      2'b01: y = b;
      default: y = c;
    endcase
  end
endmodule"""
    assert {"sel", "a", "b", "c"} <= cone(rtl, "y")


def test_reset_edge_is_a_dependency_of_a_register():
    rtl = """module m;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) q <= 0; else q <= d;
endmodule"""
    assert {"clk", "rst_n", "d"} <= cone(rtl, "q")


def test_multi_line_statement_is_one_assignment():
    rtl = """module m;
  assign y = a
           + b
           + c;
endmodule"""
    assert {"a", "b", "c"} <= cone(rtl, "y")
    assert S.cone(rtl, "y").lines == [2, 3, 4]


def test_concatenation_target_depends_on_the_right_hand_side_for_every_member():
    rtl = "module m; assign {carry, sum} = a + b; endmodule"
    assert {"a", "b"} <= cone(rtl, "sum") and {"a", "b"} <= cone(rtl, "carry")


def test_indexed_target_and_its_index_signal():
    rtl = "module m; always @(posedge clk) mem[addr] <= wdata; endmodule"
    assert {"addr", "wdata"} <= cone(rtl, "mem")


def test_sized_literals_do_not_create_signals():
    rtl = "module m; assign y = (a == 4'd5) | (b == 8'hFF) | c[3:0] | 1'b0; endmodule"
    sigs = cone(rtl, "y")
    assert sigs == {"y", "a", "b", "c"}


def test_comments_do_not_create_dependencies():
    rtl = """module m;
  // assign y = secret;
  assign y = a; /* assign y = other; */
endmodule"""
    assert cone(rtl, "y") == {"y", "a"}


def test_comparison_in_a_condition_is_not_an_assignment():
    rtl = """module m;
  always @(posedge clk) if (cnt <= limit) q <= d;
endmodule"""
    sigs = cone(rtl, "q")
    assert {"cnt", "limit", "d"} <= sigs
    assert "cnt" not in S.analyze(rtl)["m"].stmt_lines  # `cnt` is never assigned here


def test_instance_connections_keep_the_cone_from_stopping_at_the_boundary():
    rtl = """module top(input a, output z);
  wire x;
  child u0 (.i(a), .o(x));
  assign z = x;
endmodule"""
    assert {"x", "a"} <= cone(rtl, "z")


def test_unrelated_logic_is_excluded_and_transitive_register_chain_is_followed():
    rtl = """module m;
  always @(posedge clk) s1 <= in;
  always @(posedge clk) s2 <= s1;
  always @(posedge clk) other <= noise;
  assign out = s2;
endmodule"""
    sigs = cone(rtl, "out")
    assert {"s2", "s1", "in", "clk"} <= sigs and "noise" not in sigs and "other" not in sigs


def test_declaration_initializer_counts_as_an_assignment():
    rtl = "module m; wire y = a & b; endmodule"
    assert {"a", "b"} <= cone(rtl, "y")


def test_ansi_header_ports_are_declared_and_rendered_in_the_slice():
    rtl = """module m(
  input clk,
  output reg [3:0] q
);
  always @(posedge clk) q <= q + 1;
endmodule"""
    text = S.extract_coi_slice(rtl, "q", context_lines=0)
    assert "output reg [3:0] q" in text and "q <= q + 1" in text


def test_unknown_target_falls_back_to_the_full_source():
    rtl = "module m; assign y = a; endmodule"
    assert S.extract_coi_slice(rtl, "nope") == rtl
    assert S.cone(rtl, "a").found is False  # an input, never assigned


def test_slice_keeps_original_line_numbers_and_marks_gaps():
    rtl = "\n".join(["module m;", "  assign a = x;", "  assign junk1 = 1;", "  assign junk2 = 2;", "  assign y = a;", "endmodule"])
    text = S.extract_coi_slice(rtl, "y", context_lines=0)
    assert "   2 |   assign a = x;" in text and "   5 |   assign y = a;" in text and "..." in text
    assert "junk" not in text


def test_two_modules_are_analysed_separately():
    rtl = """module a(input i, output o); assign o = i; endmodule
module b(input j, output o); assign o = ~j; endmodule"""
    assert cone(rtl, "o", "a") == {"o", "i"} and cone(rtl, "o", "b") == {"o", "j"}


def test_compound_assignment_reads_its_own_target():
    rtl = "module m; always @(posedge clk) acc += x; endmodule"
    assert {"acc", "x"} <= cone(rtl, "acc")


def test_function_bodies_are_skipped_without_derailing_the_parse():
    rtl = """module m;
  function [3:0] f(input [3:0] v); begin f = v + 1; end endfunction
  assign y = f(a);
endmodule"""
    assert {"a"} <= cone(rtl, "y")
