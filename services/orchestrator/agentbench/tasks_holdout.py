# SPDX-License-Identifier: AGPL-3.0-or-later
"""Four tasks written AFTER the first AgentBench results, in the categories where our loop did worst (answer format, whitespace-sensitive
edits, multi-use renames, recovering from a wrong path). They are not part of the default suite, so the committed three-run results stay
comparable; select them with `--tasks h_depth_answer,h_rename_signal,h_wrong_dir,h_edge_flip`. They were written by the same person who
read the failures, so they are *new* tasks, not *independent* ones: a gain on them is weaker evidence than a gain on tasks written blind."""
from __future__ import annotations

import re

from services.orchestrator.agentbench.tasks import (
    Outcome,
    Task,
    Verdict,
    _numeric,
    _strip,
    _verdict,
)

FIFO = """// small synchronous FIFO
module fifo #(parameter int DEPTH = 24, parameter int WIDTH = 8) (
  input  logic             clk,
  input  logic             rst_n,
  input  logic             push,
  input  logic [WIDTH-1:0] din,
  output logic             full
);
  localparam int AW = $clog2(DEPTH);
  logic [AW:0] count;
  assign full = (count == DEPTH);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)              count <= '0;
    else if (push && !full)  count <= count + 1'b1;
  end
endmodule
"""

ACCUM = """module accum (
  input  logic       clk,
  input  logic       rst_n,
  input  logic [7:0] x,
  output logic [15:0] y
);
  logic [15:0] acc;   // running sum
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      acc <= 16'd0;
    end else begin
      acc <= acc + {8'd0, x};
    end
  end
  assign y = acc;
endmodule
"""

DMA = """module dma_engine #(parameter int MAX_BURST = 16, parameter int ADDR_W = 32) (
  input  logic clk,
  input  logic start,
  output logic busy
);
  always_ff @(posedge clk) busy <= start;
endmodule
"""

SHIFT = """module shift4 (
  input  logic       clk,
  input  logic       rst_n,
  input  logic       d,
  output logic [3:0] q
);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      q <= 4'b0;
    end else begin
      q <= {q[2:0], d};
    end
  end
endmodule
"""


def v_depth(o: Outcome) -> Verdict:
    return _numeric(o, 24)


def v_rename_signal(o: Outcome) -> Verdict:
    src = o.box.read("rtl/accum.sv")
    return _verdict({"old name gone from code": not re.search(r"\bacc\b", re.sub(r"//[^\n]*", "", src)),
                     "new name used at every site": len(re.findall(r"\btotal\b", src)) >= 5,
                     "module and ports kept": "moduleaccum(" in _strip(src) and "outputlogic[15:0]y" in _strip(src)})


def v_wrong_dir(o: Outcome) -> Verdict:
    return _numeric(o, 16)


def v_edge_flip(o: Outcome) -> Verdict:
    from services.orchestrator.agent.tools import ToolBox

    src = o.box.read("rtl/shift4.sv")
    s = _strip(src)
    lint = ToolBox(o.box.workspace).lint_rtl("rtl/shift4.sv")
    return _verdict({"clock edge flipped": "negedgeclk" in s and "posedgeclk" not in s,
                     "reset edge kept": "negedgerst_n" in s, "shift kept": "q<={q[2:0],d}" in s, "lint passes": lint["passed"]})


HOLDOUT: tuple[Task, ...] = (
    Task("h_depth_answer", "comprehension", "Read a parameter default (new)",
         "What is the default depth (number of entries) of the FIFO in rtl/fifo.sv? End your reply with a line of the form `ANSWER: <number>`.",
         {"rtl/fifo.sv": FIFO}, v_depth),
    Task("h_rename_signal", "editing", "Rename an internal signal at every use (new)",
         "In rtl/accum.sv rename the internal signal `acc` to `total`, everywhere it is used.",
         {"rtl/accum.sv": ACCUM}, v_rename_signal),
    Task("h_wrong_dir", "recovery", "Recover from a wrong path (new)",
         "Open rtl/dma/engine.sv and tell me the default value of its MAX_BURST parameter. End your reply with a line of the form `ANSWER: <number>`.",
         {"rtl/dma_engine.sv": DMA, "rtl/top.sv": "module top(input logic clk);\nendmodule\n"}, v_wrong_dir),
    Task("h_edge_flip", "editing", "Flip the clock edge, change nothing else (new)",
         "Make rtl/shift4.sv clock on the falling edge instead of the rising edge. Leave everything else as it is.",
         {"rtl/shift4.sv": SHIFT}, v_edge_flip, tools=("list_dir", "read_file", "grep", "write_file", "edit_file", "lint_rtl")),
)
