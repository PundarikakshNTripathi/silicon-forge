"""Unit tests for TypedPropertyIR -> SymbiYosys SVA compiler & witness parser."""
import unittest

from services.orchestrator.eda.formal_ir import FormalIRCompiler
from services.orchestrator.state import DesignContract, PortSpec


class TestFormalIR(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = FormalIRCompiler()
        self.contract = DesignContract(
            module_name="axi_lite_alu",
            clock_port="clk",
            reset_port="rst_n",
            reset_active_low=True,
            reset_sync=False,
            ports=[
                PortSpec("clk", "input", 1),
                PortSpec("rst_n", "input", 1),
                PortSpec("valid_o", "output", 1),
                PortSpec("result_o", "output", 32),
            ],
            functional_requirements=[],
        )

    def test_compile_sva_and_sby(self) -> None:
        props = [
            {"id": "P_RST", "kind": "RESET_VALUE", "signal": "valid_o", "expected": "1'b0"},
            {"id": "C_HIT", "kind": "COVER_REACHABILITY", "expression": "valid_o"},
        ]
        sva = self.compiler.compile_sva_block(self.contract, props)
        self.assertIn("f_past_valid", sva)
        self.assertIn("P_RST: assert(valid_o == 1'b0);", sva)
        self.assertIn("C_HIT: cover(valid_o);", sva)

        sby = self.compiler.render_sby_config("axi_lite_alu", "axi_lite_alu_formal.sv", depth=15)
        self.assertIn("prove: mode prove", sby)
        self.assertIn("cover: depth 15", sby)

    def test_parse_sby_witness(self) -> None:
        log = "SBY [bmc] Assert failed in axi_lite_alu: P_ZERO_FLAG at step 4"
        witnesses = self.compiler.parse_sby_witness(log)
        self.assertEqual(len(witnesses), 1)
        self.assertEqual(witnesses[0].property_id, "P_ZERO_FLAG")
        self.assertEqual(witnesses[0].cycle, 4)


if __name__ == "__main__":
    unittest.main()
