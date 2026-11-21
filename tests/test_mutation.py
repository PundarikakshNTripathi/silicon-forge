# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mutation engine: operator enumeration (pure Python) and an end-to-end run with real Icarus + Yosys (auto-skipped)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from services.orchestrator import verify


def _load_tool(name: str):
    """Import a standard-library-only tool script by path (it must be in sys.modules before @dataclass runs)."""
    spec = importlib.util.spec_from_file_location(name, verify.TOOLS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sf = _load_tool("sf_mutate")

COUNTER = """\
// a counter; wrap is a status flag the testbench never looks at
module counter #(parameter WIDTH = 4) (
  input  wire             clk,
  input  wire             rst_n,
  input  wire             en,
  input  wire             load,
  input  wire [WIDTH-1:0] d,
  output reg  [WIDTH-1:0] q,
  output wire             wrap
);
  assign wrap = en && (q == {WIDTH{1'b1}});
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) q <= 0;
    else if (load) q <= d;
    else if (en) q <= q + 1;
  end
endmodule
"""

TB = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0, rst_n = 0, en = 0, load = 0; reg [3:0] d = 0; wire [3:0] q; wire wrap; integer bad = 0;
  counter dut(.clk(clk), .rst_n(rst_n), .en(en), .load(load), .d(d), .q(q), .wrap(wrap));
  always #5 clk = ~clk;
  task check(input [3:0] want); begin #1; if (q !== want) begin bad = bad + 1; $display("BAD q=%0d want=%0d", q, want); end end endtask
  initial begin
    #12 rst_n = 1; @(negedge clk); check(0);
    d = 5; load = 1; @(negedge clk); check(5); load = 0;
    en = 1; @(negedge clk); check(6); @(negedge clk); check(7); @(negedge clk); check(8);
    en = 0; @(negedge clk); check(8); @(negedge clk); check(8);
    load = 1; d = 15; @(negedge clk); check(15); load = 0; en = 1; @(negedge clk); check(0);
    rst_n = 0; #2; check(0);
    if (bad == 0) $display("ALL PASS"); else $display("FAILED %0d", bad);
    $finish;
  end
endmodule
"""

RUN_TESTS = """\
import subprocess, sys
subprocess.run(["iverilog", "-g2005", "-o", "t.vvp", "tb.v", "rtl/counter.v"], check=True, capture_output=True)
out = subprocess.run(["vvp", "t.vvp"], capture_output=True, text=True, timeout=60).stdout
sys.exit(0 if "ALL PASS" in out else 1)
"""


# ------------------------------------------------------------------------------------------------ enumeration

def body(*lines):
    """A module with its header on its own line, like real code (a `module` line is skipped as a declaration)."""
    nl = chr(10)
    return "module m;" + nl + nl.join(lines) + nl + "endmodule" + nl


def ops(text):
    return {(m.op, m.original, m.replacement) for m in sf.enumerate_mutants(text)}


def test_binary_operators_are_swapped_and_unary_ones_left_alone():
    found = ops(body("assign y = a + b;", "assign z = -a;", "assign r = &v;", "assign s = a & b;"))
    assert ("op +->-", "+", "-") in found and ("op &->|", "&", "|") in found
    assert not any(o == "-" for _, o, _ in found)  # unary minus
    assert sum(1 for _, o, _ in found if o == "&") == 1  # the reduction `&v` is not mutated, the binary `a & b` is


def test_non_blocking_arrow_is_not_a_comparison_but_a_real_comparison_is():
    src = body("always @(posedge c) begin", "  q <= d;", "  if (a <= b) r <= 1;", "end")
    sites = [m for m in sf.enumerate_mutants(src) if m.original == "<="]
    assert len(sites) == 1 and sites[0].replacement == ">" and sites[0].line == 4  # only the comparison on line 4


def test_declarations_widths_comments_strings_and_for_headers_are_not_mutated():
    src = """module m #(parameter W = 8) (input wire [W-1:0] a, output reg [7:0] q);
  localparam K = 3;
  // a + b in a comment
  initial $display("x + y == 3");
  integer i;
  always @* begin for (i = 0; i < 4; i = i + 1) q[i] = a[i+1]; end
endmodule
"""
    assert sf.enumerate_mutants(src) == []


def test_constants_conditions_and_assignment_deletion():
    src = body("always @(posedge c) begin", "  if (en) begin", "    q <= 4'd5;", "  end else begin",
               "    q <= 4'hf;", "  end", "end")
    found = ops(src)
    assert ("const 5->6", "4'd5", "4'd6") in found
    assert ("const 15->14", "4'hf", "4'he") in found  # the largest value moves down, never overflows
    assert any(op == "negate condition" and rep == "!(en)" for op, _, rep in found)
    assert sum(1 for op, _, _ in found if op == "delete assignment") == 2


def test_unsized_and_single_bit_constants_do_not_crash_or_overflow():
    found = ops(body("assign a = 1'b1;", "assign b = 1'b0;", "assign c = 'd7;", "assign d = 9;"))
    assert ("const 1->0", "1'b1", "1'b0") in found and ("const 0->1", "1'b0", "1'b1") in found
    assert ("const 7->8", "'d7", "'d8") in found and ("const 9->10", "9", "10") in found


def test_mutant_application_is_exact_and_enumeration_is_deterministic():
    src = body("assign y = a + b;")
    a = sf.enumerate_mutants(src)
    assert a == sf.enumerate_mutants(src)
    assert sf.apply_mutant(src, a[0]) == src.replace("a + b", "a - b")


# -------------------------------------------------------------------------------------------------- real tools

@pytest.mark.real_eda
def test_mutation_run_kills_logic_bugs_and_reports_the_unchecked_output(real_backend, tmp_path):
    from services.orchestrator.config import load_flow_config
    from services.orchestrator.eda.bridge import EDABridge

    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "counter.v").write_text(COUNTER, encoding="utf-8")
    (tmp_path / "tb.v").write_text(TB, encoding="utf-8")
    (tmp_path / "run_tests.py").write_text(RUN_TESTS, encoding="utf-8")
    cfg = load_flow_config()
    cfg.eda_mode = real_backend
    runner = EDABridge(cfg).backend.runner

    report = verify.mutation_test(runner, tmp_path, ["rtl/counter.v"], "python3 run_tests.py", top="counter",
                                  max_mutants=40, seed=3, jobs=2)
    assert report["provenance"] == runner.name and report["syntax_check"] == "yosys"
    assert report["evaluated"] >= 6 and report["killed"] >= 3
    assert report["score"] is not None and 0.3 < report["score"] < 1.0
    # the testbench never checks `wrap`, so a bug there must survive, and the report must say where
    wrap_line = COUNTER.splitlines().index(next(ln for ln in COUNTER.splitlines() if "assign wrap" in ln)) + 1
    wrap_lines = {r["line"] for r in report["survivors"]}
    assert wrap_line in wrap_lines, f"expected a survivor on the unchecked `wrap` line {wrap_line}, got {sorted(wrap_lines)}"
    # a mutant is only 'equivalent' if Yosys proved it; the original file was never left modified
    assert (tmp_path / "rtl" / "counter.v").read_text(encoding="utf-8") == COUNTER


@pytest.mark.real_eda
def test_a_failing_baseline_aborts_instead_of_reporting_nonsense(real_backend, tmp_path):
    from services.orchestrator.config import load_flow_config
    from services.orchestrator.eda.bridge import EDABridge

    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "counter.v").write_text(COUNTER, encoding="utf-8")
    cfg = load_flow_config()
    cfg.eda_mode = real_backend
    with pytest.raises(verify.VerifyError, match="fails on the unmodified code"):
        verify.mutation_test(EDABridge(cfg).backend.runner, tmp_path, ["rtl/counter.v"], "false", max_mutants=3)


def test_tools_are_installed_beside_the_workspace(tmp_path):
    dest = verify.install_tools(tmp_path)
    assert dest == tmp_path / ".siliconforge" / "tools" and (dest / "sf_mutate.py").exists()
    assert Path(dest / "sf_mutate.py").read_text(encoding="utf-8") == (verify.TOOLS_DIR / "sf_mutate.py").read_text(encoding="utf-8")
