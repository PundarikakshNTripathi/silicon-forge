# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verifier-ranked repair sampling and the debug-context switch (simulated backend, replay provider, no live model)."""
import json

import pytest

from services.orchestrator.inference.providers import ReplayProvider
from services.orchestrator.pipeline import SiliconForgePipeline
from services.orchestrator.schemas import ContractModel
from services.orchestrator.state import FlowState
from tests.conftest import EXAMPLES

EX = EXAMPLES / "up_down_counter"
GOOD = (EX / "rtl" / "up_down_counter.sv").read_text(encoding="utf-8")
WRONG_NAME = GOOD.replace("module up_down_counter", "module not_the_contract_module", 1)
MISSING_PORT = GOOD.replace("input", "input wire unused_extra,\n input", 1)  # still has the module, perturbs the ports


@pytest.fixture
def contract():
    raw = json.loads((EX / "fixtures" / "contract.json").read_text(encoding="utf-8"))
    return ContractModel(**raw).to_state()


def _pipeline(sim_config, **flags):
    sim_config.pipeline.update(flags)
    return SiliconForgePipeline(sim_config, provider=ReplayProvider())


class ScriptedCoder:
    """Stands in for RtlCoderAgent.repair_rtl: returns the scripted answers in order and records the call arguments."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def repair_rtl(self, contract, rtl, diagnostic, **kw):
        self.calls.append(kw)
        return self.answers.pop(0)


def test_with_one_candidate_the_first_answer_is_used_unranked(sim_config, tmp_path, contract):
    p = _pipeline(sim_config)  # repair_candidates defaults to 1
    p.rtl_coder = ScriptedCoder([WRONG_NAME])
    out = p._repair(FlowState(run_id="r", spec_text="", eda_mode="simulated"), tmp_path, contract, GOOD, "diag", attempt=1)
    assert out == WRONG_NAME and len(p.rtl_coder.calls) == 1


def test_the_ranker_keeps_the_candidate_that_passes_the_guardrails_even_if_it_is_not_first(sim_config, tmp_path, contract):
    p = _pipeline(sim_config, repair_candidates=3)
    p.rtl_coder = ScriptedCoder([WRONG_NAME, GOOD, MISSING_PORT])
    state = FlowState(run_id="r", spec_text="", eda_mode="simulated")
    out = p._repair(state, tmp_path, contract, GOOD, "diag", attempt=1)
    assert out == GOOD
    ev = [e for e in state.events if e["stage"] == "RTL_REPAIR"][-1]
    assert ev["payload"]["picked"] == 1 and ev["payload"]["scores"][1]["guardrails"] == 1
    assert ev["payload"]["scores"][0]["guardrails"] == 0
    # candidate 0 is the low-temperature answer, the others explore
    temps = [c.get("temperature", 0.05) for c in p.rtl_coder.calls]
    assert temps == [0.05, 0.7, 0.7]


def test_identical_candidates_are_counted_once(sim_config, tmp_path, contract):
    p = _pipeline(sim_config, repair_candidates=3)
    p.rtl_coder = ScriptedCoder([GOOD, GOOD, GOOD])
    state = FlowState(run_id="r", spec_text="", eda_mode="simulated")
    assert p._repair(state, tmp_path, contract, GOOD, "diag", attempt=1) == GOOD
    ev = [e for e in state.events if e["stage"] == "RTL_REPAIR"]
    assert not ev or len(ev[-1]["payload"]["scores"]) == 1  # one distinct candidate, nothing to rank


def test_ties_keep_the_earliest_candidate(sim_config, tmp_path, contract):
    p = _pipeline(sim_config, repair_candidates=2)
    other = GOOD + "\n// variant\n"
    p.rtl_coder = ScriptedCoder([GOOD, other])
    state = FlowState(run_id="r", spec_text="", eda_mode="simulated")
    assert p._repair(state, tmp_path, contract, GOOD, "diag", attempt=1) == GOOD


def test_debug_context_can_be_switched_off_for_ablations(sim_config, tmp_path, contract):
    sentinel = object()
    on = _pipeline(sim_config)
    on.rtl_coder = ScriptedCoder([GOOD])
    on._repair(FlowState(run_id="r", spec_text="", eda_mode="simulated"), tmp_path, contract, GOOD, "d", attempt=1,
               witness=sentinel)
    off = _pipeline(sim_config, debug_context=False)
    off.rtl_coder = ScriptedCoder([GOOD])
    off._repair(FlowState(run_id="r", spec_text="", eda_mode="simulated"), tmp_path, contract, GOOD, "d", attempt=1,
                witness=sentinel)
    assert on.rtl_coder.calls[0]["witness"] is sentinel and off.rtl_coder.calls[0]["witness"] is None
