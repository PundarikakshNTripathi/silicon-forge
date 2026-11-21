# SPDX-License-Identifier: AGPL-3.0-or-later
"""SF-RTL-005 must not report a less-or-equal comparison in always_comb as a non-blocking assignment (C++ linter)."""
import pytest

from services.orchestrator.config import load_flow_config
from services.orchestrator.guardrails.hallucination import HallucinationGuardrail

COMPARISONS = """
module cmp (input logic [7:0] count, input logic [7:0] limit, output logic done, output logic [7:0] y);
  always_comb begin
    done = 1'b0;
    y = 8'd0;
    if (count <= limit) done = 1'b1;
    y = (count <= limit) ? count : limit;
  end
endmodule
"""
REAL = """
module bad (input logic en, input logic [7:0] d, output logic [7:0] q);
  always_comb begin
    if (en) q <= d;
  end
endmodule
"""


@pytest.fixture(scope="module")
def guard():
    cfg = load_flow_config()
    binary = cfg.telemetry_binary
    if not binary or not binary.exists():
        pytest.skip("the C++ telemetry binary is not built (python scripts/dev.py build-cpp)")
    return HallucinationGuardrail(cfg.pdk_spec, binary)


def rules(guard, rtl):
    return [v.rule_id for v in guard._run_cpp_static_lint(rtl)]


def test_comparisons_in_always_comb_pass(guard):
    assert "SF-RTL-005" not in rules(guard, COMPARISONS)


def test_a_real_nonblocking_assignment_in_always_comb_is_still_an_error(guard):
    assert rules(guard, REAL).count("SF-RTL-005") == 1
