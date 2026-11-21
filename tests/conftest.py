# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared fixtures. Tests never call live LLM APIs; real-EDA tests auto-skip when the toolchain is absent."""
from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path

import pytest

from services.orchestrator.config import load_flow_config
from services.orchestrator.eda.bridge import EDABridge
from services.orchestrator.schemas import PROPERTY_LIST, ContractModel

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "hardware" / "examples"
FIXTURES = ROOT / "tests" / "fixtures"


def example_names() -> list[str]:
    return sorted(p.parent.parent.name for p in EXAMPLES.glob("*/fixtures/contract.json"))


def load_example(name: str):
    fx = EXAMPLES / name / "fixtures"
    contract = ContractModel.model_validate_json((fx / "contract.json").read_text(encoding="utf-8")).to_state()
    props = [p.model_dump() for p in PROPERTY_LIST.validate_python(json.loads((fx / "props.json").read_text(encoding="utf-8")))]
    good = next((EXAMPLES / name / "rtl").glob("*.sv")).read_text(encoding="utf-8")
    bug = (fx / "rtl_bug.sv").read_text(encoding="utf-8")
    return contract, props, good, bug


def _real_backend_name() -> str | None:
    """Pick a real EDA backend if its tools are present (native first, then WSL)."""
    cfg = load_flow_config()
    candidates = ["native"] + (["wsl"] if platform.system() == "Windows" and shutil.which("wsl") else [])
    for mode in candidates:
        cfg.eda_mode = mode
        try:
            tools = EDABridge(cfg).check_tools()
        except Exception:  # noqa: BLE001
            continue
        if tools.get("sby") and tools.get("yosys"):
            return mode
    return None


@pytest.fixture(scope="session")
def real_backend() -> str:
    mode = _real_backend_name()
    if mode is None:
        pytest.skip("no real EDA backend (sby + yosys) available")
    return mode


@pytest.fixture()
def sim_config(tmp_path):
    cfg = load_flow_config()
    cfg.eda_mode = "simulated"
    cfg.workspace_root = tmp_path
    return cfg


def pytest_configure(config):
    config.addinivalue_line("markers", "real_eda: needs real Yosys/SymbiYosys (auto-skipped when absent)")
    config.addinivalue_line("markers", "real_pnr: needs LibreLane (slow, minutes)")
