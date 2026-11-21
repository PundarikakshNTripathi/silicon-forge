# SPDX-License-Identifier: AGPL-3.0-or-later
"""Debug context: waveform window, earliest divergence, attempt memory, lint-line extraction, repair prompt."""
from pathlib import Path
from types import SimpleNamespace

from services.orchestrator.agents.rtl_coder import RtlCoderAgent
from services.orchestrator.debug import (
    AttemptMemory,
    build_repair_context,
    error_lines,
    first_divergence,
    from_wavejson,
    step_time,
    waveform_window,
)
from services.orchestrator.eda.vcd import parse_vcd

VCD = """$timescale 1ns $end
$scope module top $end
$var wire 1 ! clk $end
$var wire 1 " wr_en $end
$var wire 4 # count [3:0] $end
$var wire 1 $ full_o $end
$var wire 32 % smt_step [31:0] $end
$upscope $end
$enddefinitions $end
#0
0!
0"
b0000 #
0$
b00000000000000000000000000000000 %
#10
1!
1"
b00000000000000000000000000000001 %
#20
0!
#30
1!
b1111 #
1$
b00000000000000000000000000000010 %
"""


def trace():
    return parse_vcd(VCD)


def test_wavejson_round_trip_keeps_values():
    tr = trace()
    back = from_wavejson(tr.to_wavejson())
    assert back.signals == tr.signals and back.value_at("count", 30) == tr.value_at("count", 30)


def test_step_time_uses_the_smt_step_signal():
    tr = trace()
    assert step_time(tr, 1) == 10 and step_time(tr, 2) == 30 and step_time(tr, 99) == tr.end_time


def test_window_marks_the_failing_time_and_collapses_unchanged_values():
    text = waveform_window(trace(), fail_time=30, mark="full_o", before=3)
    head, *rows = text.splitlines()[1:]
    assert ">full_o" in head and head.rstrip().endswith(">full_o")  # marked signal is the last column
    last = rows[-1]
    assert last.split()[0] == "@30" and last.split()[-1] == "1"
    assert "." in rows[1]  # a value that did not change since the previous row is shown as '.'
    assert "smt_step" not in head  # the formal bookkeeping signal is not a design signal


def test_window_limits_rows_before_the_failure():
    tr = trace()
    full = waveform_window(tr, fail_time=30, before=10, after=0).splitlines()
    short = waveform_window(tr, fail_time=30, before=1, after=0).splitlines()
    assert len(short) < len(full)


def test_first_divergence_finds_the_earliest_difference_and_the_signals():
    a, b = trace(), trace()
    assert first_divergence(a, b) is None
    b.changes["count"] = [(0, "0000"), (30, "1110")]
    assert first_divergence(a, b) == (30, ["count"])


def test_error_lines_point_at_the_lines_a_lint_error_names():
    rtl = "module m;\n  wire a;\n  assign a = b +;\n  assign c = a;\nendmodule"
    diag = "%Error: /tmp/run/dut.sv:3:17: syntax error, unexpected ';'\n%Warning-UNUSED: dut.sv:2:8: unused"
    text = error_lines(diag, rtl)
    assert ">>   3 |   assign a = b +;" in text and ">>   2 |   wire a;" in text
    assert error_lines("no locations here", rtl) == "" and error_lines("x.sv:999: y", rtl) == ""


def test_attempt_memory_records_diffs_and_detects_a_stuck_loop():
    mem = AttemptMemory()
    mem.record("repair-1", "assertion P1 failed at step 5", "assign y = a;", "assign y = a & b;")
    assert not mem.stuck() and mem.repeats_last("assertion P1 failed at step 9")  # same failure modulo numbers
    mem.record("repair-2", "assertion P1 failed at step 7", "assign y = a & b;", "assign y = a | b;")
    assert mem.stuck()
    text = mem.render()
    assert "Attempt 1 (repair-1)" in text and "+assign y = a & b;" in text and "-assign y = a;" in text


def test_attempt_memory_keeps_the_most_recent_attempts_when_clipped():
    mem = AttemptMemory()
    for k in range(30):
        mem.record(f"r{k}", f"failure number {k} " + "x" * 300, "a", "b")
    text = mem.render(max_chars=800)
    assert "older attempts omitted" in text and "r29" in text and "r0)" not in text


def test_repair_context_combines_the_sections_that_have_content():
    tr = trace()
    witness = SimpleNamespace(trace=tr.to_wavejson(), cycle=2, property_id="P_full")
    mem = AttemptMemory()
    mem.record("formal-repair-1", "P_full failed", "assign full_o = 0;", "assign full_o = count == 15;")
    rtl = "module m;\n  assign full_o = (count == 15);\n  always @(posedge clk) if (wr_en) count <= count + 1;\nendmodule"
    ctx = build_repair_context(rtl, "P_full failed", "full_o", witness, mem)
    assert "P_full" in ctx and "Waveform window" in ctx and "wr_en" in ctx and "Do not repeat these" in ctx
    assert build_repair_context(rtl, "plain message") == ""  # nothing to add, nothing added


class _Router:
    def __init__(self):
        self.prompts = []

    def complete(self, **kw):
        self.prompts.append(kw["user_prompt"])
        return SimpleNamespace(content="```systemverilog\nmodule m; endmodule\n```")


def test_repair_prompt_carries_the_evidence_and_the_attempt_history():
    prompts_dir = Path(__file__).resolve().parents[1] / "configs" / "prompts"
    router = _Router()
    agent = RtlCoderAgent(router, skills=None, prompts_dir=prompts_dir)
    mem = AttemptMemory()
    mem.record("guardrail-repair-1", "%Error: dut.sv:2:3: bad", "module m;", "module m; endmodule")
    out = agent.repair_rtl(SimpleNamespace(module_name="m"), "module m;\n  oops\nendmodule",
                           "%Error: dut.sv:2:3: syntax error", retry_attempt=2, memory=mem)
    prompt = router.prompts[0]
    assert out == "module m; endmodule"
    assert "Evidence:" in prompt and ">>   2 |   oops" in prompt and "Attempt 1 (guardrail-repair-1)" in prompt
