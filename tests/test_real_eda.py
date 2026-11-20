"""Real Yosys / SymbiYosys checks on every bundled example (auto-skipped when tools are absent)."""
from __future__ import annotations

import pytest

from services.orchestrator.config import load_flow_config
from services.orchestrator.eda.bridge import EDABridge
from tests.conftest import example_names, load_example

pytestmark = pytest.mark.real_eda


@pytest.fixture()
def bridge(real_backend):
    cfg = load_flow_config()
    cfg.eda_mode = real_backend
    return EDABridge(cfg)


@pytest.mark.parametrize("name", example_names())
def test_good_rtl_is_proven_and_equivalent(bridge, tmp_path, name):
    contract, props, good, _ = load_example(name)
    rep, _ = bridge.run_formal_verification(tmp_path, contract, good, props)
    assert rep.mode == bridge.mode != "simulated"
    assert rep.passed, rep.sby_log[-1500:]
    assert rep.task_status == {"bmc": "PASS", "prove": "PASS", "cover": "PASS"}
    assert bridge.run_synthesis_and_lec(tmp_path, contract, good).passed


@pytest.mark.parametrize("name", example_names())
def test_injected_bug_is_caught_with_a_real_witness(bridge, tmp_path, name):
    contract, props, _, bug = load_example(name)
    rep, _ = bridge.run_formal_verification(tmp_path, contract, bug, props)
    assert not rep.passed
    w = rep.witnesses[0]
    assert w.failing_signal in contract.port_names()
    assert w.trace is not None and w.stimulus
