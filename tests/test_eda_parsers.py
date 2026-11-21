# SPDX-License-Identifier: AGPL-3.0-or-later
"""LibreLane metrics parsing (real metrics.json), runner path translation, and config generation."""
from __future__ import annotations

import json
from pathlib import Path

from services.orchestrator.eda.librelane import LibreLaneFlow
from services.orchestrator.eda.runner import NativeRunner, WslRunner
from tests.conftest import FIXTURES, load_example


def _metrics():
    return json.loads((FIXTURES / "librelane_metrics_axi_lite_alu.json").read_text(encoding="utf-8"))


def test_worst_corner_is_the_default():
    ppa = LibreLaneFlow.parse_metrics(_metrics(), {"CLOCK_PERIOD": 10.0})
    # real result: the ss_100C_1v60 corner misses 10 ns for the 32-bit ALU
    assert ppa.wns_ns < 0
    assert ppa.wns_ns == min(ppa.corner_wns.values())
    assert ppa.drc_violations == 0 and ppa.lvs_clean
    assert ppa.total_power_mw > 0 and ppa.area_um2 > 0
    assert ppa.extra["die_area_um2"] > ppa.area_um2


def test_typical_corner_selection_closes_timing():
    ppa = LibreLaneFlow.parse_metrics(_metrics(), {}, timing_corner="tt")
    assert ppa.wns_ns > 0  # the same design meets 10 ns at the typical corner


def test_wsl_quoting_neutralises_shell_expansion_but_keeps_home_expandable():
    q = WslRunner._q
    assert q("$PDK_ROOT/x") == "'\\$PDK_ROOT/x'"
    assert q("$(rm -rf x)") == "'\\$(rm -rf x)'"
    assert q("a`id`b") == "'a\\`id\\`b'"
    assert q("$HOME/eda/x") == '"$HOME"/' + "eda/x"  # the one deliberate expansion
    assert q("$HOME/a$b") == '"$HOME"/' + "'a\\$b'"
    assert q("plain path") == "'plain path'"


def test_wsl_path_translation():
    assert WslRunner.to_wsl_path(Path("C:/Users/dev/proj/runs")) == "/mnt/c/Users/dev/proj/runs"


def test_librelane_config_uses_real_variable_names():
    contract, *_ = load_example("up_down_counter")
    cfg = LibreLaneFlow(NativeRunner()).build_config(
        contract, "up_down_counter.sv",
        {"SYNTH_STRATEGY": "DELAY 4", "FP_CORE_UTIL": 45, "PL_TARGET_DENSITY_PCT": 55, "GRT_ADJUSTMENT": 0.3,
         "CLOCK_PERIOD": 12.5}, "sky130A")
    assert cfg["DESIGN_NAME"] == "up_down_counter" and cfg["CLOCK_PERIOD"] == 12.5
    assert cfg["VERILOG_FILES"] == "dir::up_down_counter.sv" and cfg["FP_CORE_UTIL"] == 45
