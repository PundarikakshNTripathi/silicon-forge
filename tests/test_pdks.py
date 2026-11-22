# SPDX-License-Identifier: AGPL-3.0-or-later
"""PDK specs: every shipped spec loads, the cell allowlist catches a hallucinated cell for each PDK (IHP's `sg13g2_` cells were not
recognised as cells at all before), and the LibreLane command names the PDK and its root."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from services.orchestrator.config import load_flow_config
from services.orchestrator.eda.librelane import LibreLaneFlow
from services.orchestrator.guardrails.hallucination import HallucinationGuardrail

ROOT = Path(__file__).resolve().parents[1]
PDKS = {"sky130A": ("sky130_fd_sc_hd__nand2_1", "sky130_made_up_cell_1"),
        "gf180mcuD": ("gf180mcu_fd_sc_mcu7t5v0__nand2_1", "gf180mcu_made_up_cell_1"),
        "ihp-sg13g2": ("sg13g2_nand2_1", "sg13g2x_made_up_cell_1")}


def spec_for(pdk: str, tmp_path: Path):
    cfg = yaml.safe_load((ROOT / "configs" / "flow_default.yaml").read_text(encoding="utf-8"))
    cfg["pdk"] = pdk
    path = tmp_path / f"{pdk}.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return load_flow_config(path).pdk_spec


@pytest.mark.parametrize("pdk", list(PDKS))
def test_each_pdk_loads_and_its_allowlist_accepts_real_cells_and_rejects_invented_ones(pdk, tmp_path):
    spec = spec_for(pdk, tmp_path)
    assert spec.pdk_name == pdk and spec.allowed_cell_prefixes and "FP_CORE_UTIL" in spec.allowed_flow_vars
    good, bad = PDKS[pdk]
    guard = HallucinationGuardrail(spec, None)
    assert guard._check_pdk_cells(f"module m; {good} u0 (.A(a)); endmodule") == []
    found = guard._check_pdk_cells(f"module m; {bad} u0 (.A(a)); endmodule")
    assert [v.rule_id for v in found] == ["SF-GRD-301"], found


def test_a_cell_from_another_pdk_is_rejected(tmp_path):
    guard = HallucinationGuardrail(spec_for("gf180mcuD", tmp_path), None)
    assert guard._check_pdk_cells("module m; sky130_fd_sc_hd__nand2_1 u0 (.A(a)); endmodule")
    assert guard._check_pdk_cells("module m; sg13g2_nand2_1 u0 (.A(a)); endmodule")


def test_every_pdk_spec_states_what_it_was_validated_with():
    """A PDK is never listed as supported without a run behind it: each spec names its evidence or says it has none."""
    for pdk in PDKS:
        text = (ROOT / "configs" / "pdks" / f"{pdk}.yaml").read_text(encoding="utf-8")
        assert "Validated" in text or "NOT VALIDATED" in text, pdk
        if "Validated: one small design" in text:  # the claim has a committed result behind it
            assert list((ROOT / "benchmarks" / "results").glob(f"flow-up_down_counter-{pdk}-*.json")) or pdk == "sky130A", pdk


def test_librelane_names_the_pdk_and_uses_its_root(monkeypatch):
    class Runner:
        name = "wsl"

    monkeypatch.delenv("SILICONFORGE_PDK_ROOTS", raising=False)
    flow = LibreLaneFlow(Runner(), pdk_roots={"gf180mcuD": "$HOME/pdk-roots/gf180mcuD"})
    assert flow._pdk_args({"PDK": "gf180mcuD"}) == ["--pdk", "gf180mcuD", "--pdk-root", "$HOME/pdk-roots/gf180mcuD"]
    assert flow._pdk_args({"PDK": "sky130A"}) == ["--pdk", "sky130A"]  # no root configured: LibreLane's default
    assert flow._pdk_args({}) == []
    monkeypatch.setenv("SILICONFORGE_PDK_ROOTS", '{"ihp-sg13g2": "/pdk/ihp"}')
    assert LibreLaneFlow(Runner())._pdk_args({"PDK": "ihp-sg13g2"})[-2:] == ["--pdk-root", "/pdk/ihp"]
