"""Tiny Tapeout (IHP SG13CMOS5L) support: validation, budget, scaffold, preflight; real synthesis + gate-level simulation."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

from services.orchestrator import tt, verify
from services.orchestrator.tt.project import UTIL_COMFORTABLE, die_area_um2

TOP = "tt_um_demo_counter"

COUNTER_V = """\
`default_nettype none

module tt_um_demo_counter (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  reg [7:0] count;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) count <= 8'd0;
    else if (ui_in[0]) count <= count + 8'd1;
  end
  assign uo_out  = count;
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, uio_in, ui_in[7:1], 1'b0};
endmodule
"""

COUNTER_TEST = """import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge


@cocotb.test()
async def test_counts_when_enabled(dut):
    # Drive and sample on the falling edge, so the result never depends on delta-cycle ordering around the rising edge
    # (RTL and gate-level netlists order those differently).
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    assert dut.uo_out.value == 0
    dut.ui_in.value = 1
    await ClockCycles(dut.clk, 7)
    await FallingEdge(dut.clk)
    assert dut.uo_out.value == 7
    dut.ui_in.value = 0
    await ClockCycles(dut.clk, 5)
    await FallingEdge(dut.clk)
    assert dut.uo_out.value == 7
"""


def make_project(tmp_path: Path, **kw) -> Path:
    root = tmp_path / "proj"
    tt.scaffold(root, TOP, title="Demo counter", author="Test", description="counts", tiles=kw.get("tiles", "1x1"))
    (root / "src" / "project.v").write_text(COUNTER_V, encoding="utf-8")
    (root / "test" / "test.py").write_text(COUNTER_TEST, encoding="utf-8")
    return root


# ----------------------------------------------------------------------------------------------------- scaffold

def test_scaffold_writes_the_official_layout(tmp_path):
    root = make_project(tmp_path)
    for rel in ("info.yaml", "src/project.v", "src/config.json", "test/tb.v", "test/test.py", "docs/info.md",
                ".github/workflows/gds.yaml"):
        assert (root / rel).is_file(), rel
    info = yaml.safe_load((root / "info.yaml").read_text())
    assert info["project"]["top_module"] == TOP and info["yaml_version"] == 6 and len(info["pinout"]) == 24
    assert "pdk: ihp-sg13cmos5l" in (root / ".github" / "workflows" / "gds.yaml").read_text()
    cfg = json.loads((root / "src" / "config.json").read_text())
    assert cfg["CLOCK_PORT"] == "clk" and cfg["PL_TARGET_DENSITY_PCT"] == 60
    with pytest.raises(FileExistsError):
        tt.scaffold(root, TOP)
    with pytest.raises(ValueError, match="tt_um_"):
        tt.scaffold(tmp_path / "x", "my_design")
    with pytest.raises(ValueError, match="tiles"):
        tt.scaffold(tmp_path / "y", TOP, tiles="9x9")


def test_a_fresh_scaffold_fails_only_on_the_blank_datasheet_fields(tmp_path):
    root = tmp_path / "fresh"
    tt.scaffold(root, TOP)
    rep = tt.preflight(root)
    failed = {c.name for c in rep.checks if c.status == "fail"}
    assert failed == {"info.yaml project.title", "info.yaml project.author", "info.yaml project.description"}
    assert any(c.name == "pinout described" and c.status == "warn" for c in rep.checks)
    filled = tmp_path / "filled"
    tt.scaffold(filled, TOP, title="T", author="A", description="D")
    assert tt.preflight(filled).ok  # everything else about the skeleton is valid


# ------------------------------------------------------------------------------------------------ validation

def test_info_problems_are_reported_individually(tmp_path):
    root = make_project(tmp_path)
    info = yaml.safe_load((root / "info.yaml").read_text())
    info["project"].update(top_module="my_design", tiles="9x9", source_files=["nope.v"], title="")
    del info["pinout"]["uio[3]"]
    (root / "info.yaml").write_text(yaml.safe_dump(info))
    rep = tt.preflight(root)
    failed = {c.name for c in rep.checks if c.status == "fail"}
    assert {"info.yaml project.title", "top_module name", "tiles", "source_files exist in src/",
            "pinout has all 24 pins"} <= failed
    assert not rep.ok


def test_source_checks_find_missing_ports_unused_reset_and_simulation_only_constructs(tmp_path):
    root = make_project(tmp_path)
    (root / "src" / "project.v").write_text(
        "module tt_um_demo_counter(input wire [7:0] ui_in, output wire [7:0] uo_out, input wire clk);\n"
        "  initial begin #5 end\n  assign uo_out = ui_in;\nendmodule\n", encoding="utf-8")
    rep = tt.preflight(root)
    by = {c.name: c for c in rep.checks}
    assert by["required ports"].status == "fail" and "rst_n" in by["required ports"].detail
    assert by["no initial blocks"].status == "warn" and by["no delays"].status == "warn"
    assert by["reset is used"].status == "warn"


def test_a_missing_top_module_is_a_failure(tmp_path):
    root = make_project(tmp_path)
    (root / "src" / "project.v").write_text("module something_else(); endmodule\n", encoding="utf-8")
    assert not tt.preflight(root).ok


def test_comments_do_not_hide_or_fake_declarations(tmp_path):
    root = make_project(tmp_path)
    src = COUNTER_V.replace("input  wire       rst_n", "// input wire rst_n\n    input wire x")
    (root / "src" / "project.v").write_text(src.replace(" else if (ui_in[0])", " else if (ui_in[0])"), encoding="utf-8")
    by = {c.name: c for c in tt.preflight(root).checks}
    assert by["required ports"].status == "fail" and "rst_n" in by["required ports"].detail


# --------------------------------------------------------------------------------------------------- budget

def test_tile_areas_match_the_published_die_sizes():
    assert round(die_area_um2("8x4")) == 1_225_257 and round(die_area_um2("1x1")) == 31_318
    assert die_area_um2("8x4") > die_area_um2("6x4") > die_area_um2("4x4") > die_area_um2("1x1")


def test_budget_picks_the_smallest_tile_that_fits_and_says_when_nothing_does():
    b = tt.budget(43_544, "1x1")  # pio16 as synthesized: bigger than a 1x1 die
    assert b["chosen"]["verdict"] == "too small" and b["smallest_that_fits"] == "2x2"
    two = next(r for r in b["options"] if r["tiles"] == "2x2")
    assert two["verdict"] == "comfortable" and two["utilization"] <= UTIL_COMFORTABLE
    assert tt.budget(10_000_000)["smallest_that_fits"] is None
    assert tt.budget(1000, margin=1.0)["options"][0]["utilization"] == pytest.approx(1000 / die_area_um2("1x1"), abs=1e-3)


def test_preflight_uses_the_synthesis_report_for_area_and_latches(tmp_path):
    root = make_project(tmp_path, tiles="1x1")
    synth = {"ok": True, "area_um2": 40_000.0, "latches": 3, "sequential_area_um2": 20_000.0}
    by = {c.name: c for c in tt.preflight(root, synth).checks}
    assert by["area vs tiles (post-synthesis)"].status == "fail" and "smallest that fits" in by["area vs tiles (post-synthesis)"].detail
    assert by["latches"].status == "warn" and "50%" in by["sequential area share"].detail
    assert by["timing, DRC, LVS"].status == "info"  # honest about what was not checked


def test_checklist_never_claims_to_submit(tmp_path):
    text = tt.submission_checklist(tmp_path, {"project": {"title": "X"}})
    assert "Nothing here is submitted for you" in text and "- [ ]" in text


def test_docker_guard_reports_a_clear_reason(monkeypatch):
    monkeypatch.setattr(tt.shutil, "which", lambda name: None)
    ok, why = tt.docker_ready()
    assert not ok and "not installed" in why


# ------------------------------------------------------------------------------------------- synth parsing

def _load(name):
    spec = importlib.util.spec_from_file_location(name, verify.TOOLS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


STAT = """\
   Number of cells:               8
      2   25.402   sg13cmos5l_a21o_1
      559 2.74E+04   sg13cmos5l_dfrbpq_1
      4    40.0   sg13cmos5l_dlhq_1
      17   92.534   sg13cmos5l_inv_1
   Chip area for module '\\top': 29000.5
"""


def test_a_design_with_no_cells_reports_zero_area_and_preflight_flags_it(tmp_path):
    """Regression: Yosys prints no area line when nothing is left after synthesis (the scaffold's placeholder design)."""
    s = _load("sf_tt_synth").parse_stat("   Number of cells:                0\n")
    assert s["cells"] == 0 and s["area_um2"] == 0.0
    root = make_project(tmp_path)
    by = {c.name: c for c in tt.preflight(root, {"ok": True, "area_um2": 0.0, "latches": 0, "sequential_area_um2": 0.0}).checks}
    assert by["design is empty"].status == "warn"
    assert tt.preflight(root, {"ok": True, "area_um2": None, "latches": 0}).ok  # unknown area is not a crash


def test_stat_parser_separates_flops_latches_and_sequential_area():
    s = _load("sf_tt_synth").parse_stat(STAT)
    assert s["cells"] == 582 and s["flops"] == 559 and s["latches"] == 4 and s["area_um2"] == 29000.5
    assert s["sequential_area_um2"] == pytest.approx(27400 + 40.0)


# ----------------------------------------------------------------------------- real tools: IHP PDK + Yosys

@pytest.mark.real_eda
def test_runner_passes_dollar_and_backticks_to_the_program_literally(real_backend, tmp_path):
    """`wsl.exe` hands the script to a shell that expands $VAR, $(cmd) and backticks even inside single quotes; the
    runner must neutralise that, or an argument such as a file name could run commands."""
    from services.orchestrator.config import load_flow_config
    from services.orchestrator.eda.bridge import EDABridge

    cfg = load_flow_config()
    cfg.eda_mode = real_backend
    runner = EDABridge(cfg).backend.runner
    (tmp_path / "probe.py").write_text("import sys\nprint(repr(sys.argv[1:]))\n", encoding="utf-8")
    args = ["$PDK_ROOT/x", "$(echo INJECTED)", "a`echo INJECTED`b", "back\\slash", 'dq"uote']
    res = runner.run(["python3", "probe.py", *args], tmp_path)
    assert res.ok and "INJECTED" not in res.stdout.replace("echo INJECTED", "")
    assert res.stdout.strip() == repr(args)


def _runner(real_backend):
    from services.orchestrator.config import load_flow_config
    from services.orchestrator.eda.bridge import EDABridge

    cfg = load_flow_config()
    cfg.eda_mode = real_backend
    return EDABridge(cfg).backend.runner


@pytest.fixture()
def ihp_runner(real_backend):
    runner = _runner(real_backend)
    probe = runner.run(["test", "-d", "$HOME/pdk/ihp-sg13cmos5l/libs.ref"], Path.cwd(), timeout=60)
    if not probe.ok:
        pytest.skip("IHP sg13cmos5l PDK is not installed (ciel enable --pdk-family ihp-sg13cmos5l ...)")
    return runner


@pytest.mark.real_eda
def test_real_synthesis_reports_ihp_cells_and_area(ihp_runner, tmp_path):
    root = make_project(tmp_path)
    rep = tt.synthesize(ihp_runner, root)
    assert rep["provenance"] == ihp_runner.name and rep["pdk"] == "ihp-sg13cmos5l"
    assert rep["flops"] == 8 and rep["latches"] == 0 and 200 < rep["area_um2"] < 3000
    assert any(n.startswith("sg13cmos5l_") for n in rep["by_cell"]) and "post-synthesis" in rep["note"]
    b = tt.budget(rep["area_um2"], "1x1")
    assert b["chosen"]["verdict"] == "comfortable"
    assert tt.preflight(root, rep).ok


@pytest.mark.real_eda
def test_gate_level_simulation_runs_the_projects_own_tests_on_the_netlist(ihp_runner, tmp_path):
    root = make_project(tmp_path)
    # the same test must pass on the RTL first, or a gate-level pass/fail says nothing
    rtl = verify.run_cocotb(ihp_runner, root, sources=["src/project.v", "test/tb.v"], top="tb", test_module="test",
                            test_dir="test", build_dir=".siliconforge/sim_build_rtl", out=".siliconforge/rtl.json")
    assert rtl["ok"], rtl["failure_excerpts"]
    tt.synthesize(ihp_runner, root)
    gls = tt.run_gls(ihp_runner, root)
    assert gls["ok"] and gls["passed"] == 1 and "post-synthesis" in gls["netlist_kind"]
    # and it really is the gate-level netlist: break the netlist's behaviour and the same test must fail
    net = root / ".siliconforge" / "tt" / "netlist.v"
    text = net.read_text(encoding="utf-8")
    assert "sg13cmos5l_dfrbpq_1" in text
    assert ".RESET_B(rst_n)" in text
    net.write_text(text.replace(".RESET_B(rst_n)", ".RESET_B(1'b1)"), encoding="utf-8")  # reset no longer reaches the flops
    broken = tt.run_gls(ihp_runner, root)
    assert not broken["ok"]
