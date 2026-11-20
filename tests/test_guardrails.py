"""Unit tests for the six-gate HallucinationGuardrail and RtlDependencySlicer."""
from pathlib import Path
import unittest

from services.orchestrator.config import load_flow_config
from services.orchestrator.guardrails.hallucination import HallucinationGuardrail
from services.orchestrator.guardrails.rtl_slicer import RtlDependencySlicer
from services.orchestrator.state import DesignContract, PortSpec


class TestGuardrails(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_flow_config()
        self.grd = HallucinationGuardrail(self.cfg.pdk_spec, self.cfg.telemetry_binary)
        self.contract = DesignContract(
            module_name="mini_adder",
            clock_port="clk",
            reset_port="rst_n",
            reset_active_low=True,
            reset_sync=False,
            ports=[
                PortSpec("clk", "input", 1),
                PortSpec("rst_n", "input", 1),
                PortSpec("a_i", "input", 8),
                PortSpec("y_o", "output", 8),
            ],
            functional_requirements=["Registers a_i to y_o"],
        )

    def test_valid_rtl_passes(self) -> None:
        rtl = """module mini_adder (
            input logic clk,
            input logic rst_n,
            input logic [7:0] a_i,
            output logic [7:0] y_o
        );
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) y_o <= 8'd0;
                else y_o <= a_i;
            end
        endmodule"""
        rep = self.grd.audit_rtl(rtl, self.contract)
        self.assertTrue(rep.passed, f"Unexpected violations: {rep.violations}")

    def test_hallucinated_pdk_cell_and_delay_blocked(self) -> None:
        rtl = """module mini_adder (
            input logic clk, input logic rst_n, input logic [7:0] a_i, output logic [7:0] y_o
        );
            tsmc5nm_fake_buf u0 (.A(a_i[0]), .Y(y_o[0]));
            always_ff @(posedge clk) begin
                #5 y_o = a_i;
            end
        endmodule"""
        rep = self.grd.audit_rtl(rtl, self.contract)
        self.assertFalse(rep.passed)
        rule_ids = {v.rule_id for v in rep.violations}
        self.assertIn("SF-GRD-301", rule_ids)
        self.assertIn("SF-RTL-001", rule_ids)

    def test_hallucinated_commercial_tcl_and_clock_floor_blocked(self) -> None:
        rep = self.grd.audit_flow_config(
            flow_vars={"CLOCK_PERIOD": 1.0, "FAKE_KNOB_XYZ": 99},
            tcl_snippet="compile_ultra -gate_clock",
        )
        self.assertFalse(rep.passed)
        rule_ids = {v.rule_id for v in rep.violations}
        self.assertIn("SF-GRD-401", rule_ids)
        self.assertIn("SF-GRD-402", rule_ids)
        self.assertIn("SF-GRD-403", rule_ids)

    def test_vacuous_formal_ir_blocked(self) -> None:
        props = [{"id": "P1", "kind": "RESET_VALUE", "signal": "y_o", "expected": "8'd0"}]
        rep = self.grd.audit_formal_ir(props, self.contract)
        self.assertFalse(rep.passed)
        self.assertTrue(any(v.rule_id == "SF-GRD-502" for v in rep.violations))

    def test_rtl_coi_slicer_extracts_target_cone(self) -> None:
        rtl = "\n".join([
            "module m;",
            "  assign tmp = a_i + b_i;",
            "  assign unrelated = x_i ^ y_i;",
            "  assign zero_o = (tmp == 0);",
            "endmodule",
        ])
        slicer = RtlDependencySlicer()
        sliced = slicer.extract_coi_slice(rtl, "zero_o", context_lines=0)
        self.assertIn("tmp", sliced)
        self.assertIn("zero_o", sliced)
        self.assertNotIn("unrelated", sliced)


if __name__ == "__main__":
    unittest.main()
