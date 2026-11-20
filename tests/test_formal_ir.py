"""TypedPropertyIR -> SVA / .sby compiler, witness parsing and signal grounding."""
from __future__ import annotations

from services.orchestrator.eda.formal_ir import (
    FormalIRCompiler,
    property_signals,
    referenced_signals,
)
from services.orchestrator.eda.vcd import parse_vcd, to_int
from services.orchestrator.state import DesignContract, PortSpec
from tests.conftest import FIXTURES


def _contract() -> DesignContract:
    return DesignContract(
        module_name="axi_lite_alu", clock_port="clk", reset_port="rst_n", reset_active_low=True, reset_sync=False,
        ports=[PortSpec("clk", "input", 1), PortSpec("rst_n", "input", 1), PortSpec("valid_o", "output", 1),
               PortSpec("result_o", "output", 32), PortSpec("zero_o", "output", 1)],
        functional_requirements=[],
    )


def test_compile_sva_and_sby():
    c = FormalIRCompiler()
    props = [
        {"id": "P_RST", "kind": "RESET_VALUE", "signal": "valid_o", "expected": "1'b0"},
        {"id": "C_HIT", "kind": "COVER_REACHABILITY", "expression": "valid_o"},
    ]
    sva = c.compile_sva_block(_contract(), props)
    assert "f_past_valid" in sva
    assert "P_RST: assert(valid_o == 1'b0);" in sva
    assert "C_HIT: cover(valid_o);" in sva
    sby = c.render_sby_config("axi_lite_alu", "x.sv", depth=15, engines={"prove": "abc pdr"})
    assert "prove: mode prove" in sby and "cover: depth 15" in sby and "prove: abc pdr" in sby


def test_new_property_kinds_compile():
    c = FormalIRCompiler()
    props = [
        {"id": "P_MX", "kind": "MUTUAL_EXCLUSION", "signals": ["valid_o", "zero_o", "result_o"]},
        {"id": "P_EQ", "kind": "COMBINATIONAL_EQUIVALENCE", "expr_a": "zero_o", "expr_b": "(result_o == 32'd0)"},
        {"id": "P_LAT", "kind": "LATENCY_BOUND", "trigger": "valid_o", "response": "zero_o", "bound": 3},
    ]
    sva = c.compile_sva_block(_contract(), props)
    assert "P_MX: assert(!((valid_o) && (zero_o)) && !((valid_o) && (result_o)) && !((zero_o) && (result_o)));" in sva
    assert "P_EQ: assert((zero_o) == ((result_o == 32'd0)));" in sva
    assert "f_wait_P_LAT" in sva and "P_LAT: assert(f_wait_P_LAT <= " in sva


def test_signal_extraction_ignores_system_functions_and_literals():
    assert referenced_signals("count_o == $past(load_val_i) && (a == 8'hFF)") == {"count_o", "load_val_i", "a"}
    sigs = property_signals({"kind": "LATENCY_BOUND", "trigger": "start_i && !busy_o", "response": "busy_o"})
    assert sigs == {"start_i", "busy_o"}


def test_legacy_witness_format_still_parses():
    log = "SBY [bmc] Assert failed in axi_lite_alu: P_ZERO_FLAG at step 4"
    w = FormalIRCompiler().parse_sby_witness(log)
    assert w[0].property_id == "P_ZERO_FLAG" and w[0].cycle == 4


def test_real_sby_log_and_vcd_are_parsed():
    """Fixtures are real SymbiYosys output from the injected inverted-zero_o bug."""
    log = (FIXTURES / "sby_axi_bug.log").read_text(encoding="utf-8")
    c = FormalIRCompiler()
    witnesses = c.parse_sby_witness(log)
    assert witnesses and witnesses[0].property_id == "P_ZERO_FLAG_CONSISTENT"
    assert witnesses[0].cycle == 3

    props = [{"id": "P_ZERO_FLAG_CONSISTENT", "kind": "INVARIANT", "condition": "valid_o",
              "expression": "(zero_o == (result_o == 32'd0))"}]
    # the assertion body mentions zero_o first, which is the signal to slice on (not the guard `valid_o`)
    assert c.resolve_failing_signal("P_ZERO_FLAG_CONSISTENT", props, _contract()) == "zero_o"

    trace = parse_vcd((FIXTURES / "sby_axi_bug_trace.vcd").read_text(encoding="utf-8"))
    assert {"clk", "rst_n", "zero_o", "result_o", "smt_step"} <= set(trace.signals)
    # clocked assertions are reported one step after the offending state: step 2 is valid_o=1, zero_o=0, result_o=0
    snap = trace.snapshot(20)
    assert to_int(snap["valid_o"]) == 1 and to_int(snap["zero_o"]) == 0 and to_int(snap["result_o"]) == 0
    assert trace.to_wavejson()["signals"]
