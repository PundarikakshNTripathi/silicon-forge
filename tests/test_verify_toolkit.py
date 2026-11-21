"""Verification toolkit: protocol decoders, coverage, toggle analysis (pure Python) and the pio16 lockstep regression
end to end with real Icarus + cocotb (auto-skipped when the tools are absent)."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from services.orchestrator import isa, verify

ROOT = Path(__file__).resolve().parents[1]
PIO16 = ROOT / "hardware" / "isa" / "pio16"


def _load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, verify.TOOLS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


proto = _load_tool("sf_protocols")
lock = _load_tool("sf_lockstep")
toggle = _load_tool("sf_toggle")


# ----------------------------------------------------------------------------------------------- UART

@pytest.mark.parametrize("parity", ["none", "even", "odd"])
def test_uart_round_trip(parity):
    data = bytes([0x00, 0x55, 0xA3, 0xFF, 0x7E])
    frames = proto.uart(proto.enc_uart(data, 100, start_at=500, parity=parity), 100, parity=parity)
    assert [f["byte"] for f in frames] == list(data)
    assert all(f["framing_ok"] and f["parity_ok"] and f["max_edge_error"] == 0 for f in frames)


def test_uart_reports_framing_error_parity_error_and_timing_jitter():
    good = proto.enc_uart(b"\x41", 100, start_at=500)
    # stop bit forced low: framing error
    broken = [(t, 0 if t >= 500 + 900 else v) for t, v in good]
    assert not proto.uart(broken, 100)[0]["framing_ok"]
    # wrong parity bit
    bad_par = proto.uart(proto.enc_uart(b"\x41", 100, start_at=500, parity="odd"), 100, parity="even")
    assert not bad_par[0]["parity_ok"]
    # every edge after the start bit 30% of a bit late (a uniform delay of all edges would not be jitter): it still
    # decodes, but the timing error against the start-bit grid is reported
    ideal = proto.enc_uart(b"\x55", 100, start_at=500)
    late = [(t + 30 if t > 500 else t, v) for t, v in ideal]
    f = proto.uart(late, 100)[0]
    assert f["byte"] == 0x55 and f["max_edge_error"] >= 0.25


def test_uart_ignores_a_glitch():
    edges = [(0, 1), (200, 0), (210, 1)] + proto.enc_uart(b"\x0f", 100, start_at=1000)[1:]
    assert [f["byte"] for f in proto.uart(sorted(edges), 100)] == [0x0F]


# ------------------------------------------------------------------------------------------------- SPI

@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_spi_round_trip_all_modes(mode):
    sclk, mosi, cs = proto.enc_spi(b"\x9f\x00\xa5", period=100, mode=mode)
    tx = proto.spi(sclk, mosi, None, cs, mode=mode)
    assert len(tx) == 1 and tx[0]["mosi"] == [0x9F, 0x00, 0xA5] and tx[0]["complete"]


def test_spi_wrong_mode_reads_wrong_data_and_short_transfer_is_incomplete():
    sclk, mosi, cs = proto.enc_spi(b"\xa5", period=100, mode=0)
    assert proto.spi(sclk, mosi, None, cs, mode=1)[0]["mosi"] != [0xA5]
    sclk2, mosi2, cs2 = proto.enc_spi(b"\xa5", period=100, mode=0)
    truncated = [e for e in sclk2 if e[0] < 600]
    assert not proto.spi(truncated, mosi2, None, cs2, mode=0)[0]["complete"]


# ------------------------------------------------------------------------------------------------- I2C

def test_i2c_write_with_address_data_and_stop():
    scl, sda = proto.enc_i2c(0x50, b"\x12\x34")
    ev = proto.i2c(scl, sda)
    kinds = [e["type"] for e in ev]
    assert kinds == ["start", "address", "byte", "byte", "stop"]
    assert ev[1]["address"] == 0x50 and ev[1]["read"] is False and ev[1]["ack"]
    assert [e["value"] for e in ev[2:4]] == [0x12, 0x34] and all(e["ack"] for e in ev[1:4])


def test_i2c_read_flag_and_nack_are_reported():
    scl, sda = proto.enc_i2c(0x68, b"\xff", read=True, nack_at=1)
    ev = proto.i2c(scl, sda)
    assert ev[1]["read"] is True and ev[1]["ack"] and ev[2]["ack"] is False


# ----------------------------------------------------------------------------------------------- WS2812

def test_ws2812_round_trip():
    pixels = [0xFF0000, 0x00FF00, 0x0000FF, 0x123456]
    assert proto.ws2812(proto.enc_ws2812(pixels), bit_threshold=525, reset_time=50000) == pixels


# ------------------------------------------------------------------------------------------------- VCD

VCD = """\
$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 8 " bus [7:0] $end
$upscope $end
$enddefinitions $end
#0
0!
b00000000 "
#10
1!
b00000101 "
#20
0!
b00000100 "
#30
1!
b00000000 "
"""


def test_vcd_loader_extracts_scalar_and_vector_bits_in_picoseconds(tmp_path):
    f = tmp_path / "d.vcd"
    f.write_text(VCD, encoding="utf-8")
    sig = proto.load_signals(f, ["tb.clk", "tb.bus[0]", "tb.bus[2]"])
    assert sig["tb.clk"] == [(0, 0), (10_000, 1), (20_000, 0), (30_000, 1)]
    assert sig["tb.bus[0]"] == [(0, 0), (10_000, 1), (20_000, 0)]
    assert sig["tb.bus[2]"] == [(0, 0), (10_000, 1), (30_000, 0)]


def test_toggle_coverage_counts_bits_that_move_both_ways(tmp_path):
    rep = toggle.analyze(VCD.splitlines(), scope="tb")
    by = {s["name"]: s for s in rep["per_signal"]}
    assert by["tb.clk"]["covered_bits"] == 1  # 0->1->0->1
    assert by["tb.bus"]["covered_bits"] == 2  # bit0 and bit2 rose and fell; bits 1, 3..7 never moved
    assert rep["bits"] == 9 and rep["covered_bits"] == 3 and "tb.bus[1]" in rep["not_toggled"]
    assert "not line or branch coverage" in rep["kind"]
    assert toggle.analyze(VCD.splitlines(), scope="tb", excludes=("clk",))["bits"] == 8


# ----------------------------------------------------------------------------------------- lockstep + coverage

def test_diff_state_and_mismatch_message_show_the_history():
    assert lock.diff_state({"a": 1, "b": 2}, {"a": 1, "b": 3}) == {"b": (2, 3)}
    assert lock.diff_state({"a": 1}, {"a": 2}, ignore=["a"]) == {}
    err = lock.LockstepMismatch(7, {"x": (1, 2)}, ["#6 ldx", "#7 djnz"])
    assert "after instruction #7" in str(err) and "rtl=1 model=2" in str(err) and "#6 ldx" in str(err)


def test_lockstep_driver_stops_at_the_first_divergence():
    import asyncio

    spec = isa.load_spec(PIO16 / "isa.yaml")
    prog = isa.Assembler(spec).assemble("ldx 5\nldy 6\nhalt\n")
    model = isa.Machine(spec, prog)
    truth = isa.Machine(spec, prog)
    seen = {"n": 0}

    async def step_rtl(result):
        seen["n"] += 1
        truth.step()

    def rtl_state():
        snap = truth.s.snapshot()
        if seen["n"] == 2:
            snap["y"] ^= 1  # inject a bug after the second instruction
        return snap

    with pytest.raises(lock.LockstepMismatch) as ei:
        asyncio.run(lock.lockstep(step_rtl, rtl_state, model, steps=10))
    assert ei.value.step == 2 and ei.value.diffs == {"y": (7, 6)}
    assert len(ei.value.history) == 2 and "ldy" in ei.value.history[1]


def test_cover_group_bins_crosses_and_merged_save(tmp_path):
    cov = lock.CoverGroup("g")
    cov.point("op", bins=["a", "b"])
    cov.point("v", bins={"zero": 0, "pos": lambda v: v > 0})
    cov.cross("op_v", "op", "v")
    for op, v in (("a", 0), ("a", 5), ("b", 5)):
        cov.sample("op", op)
        cov.sample("v", v)
    rep = cov.report()
    assert rep["points"]["op"]["covered"] == 2 and rep["crosses"]["op_v"]["covered"] == 3
    assert rep["crosses"]["op_v"]["missing"] == ["b|zero"]
    path = tmp_path / "c.json"
    cov.save(path)
    second = lock.CoverGroup("g")
    second.point("op", bins=["a", "b"])
    second.point("v", bins={"zero": 0, "pos": lambda v: v > 0})
    second.cross("op_v", "op", "v")
    second.sample("op", "b")
    second.sample("v", 0)  # closes the one missing cross bin
    merged = second.save(path)
    assert merged["crosses"]["op_v"]["missing"] == [] and merged["points"]["op"]["hits"]["a"] == 2
    assert json.loads(path.read_text())["coverage"] == 1.0


# ---------------------------------------------------------------- the committed pio16 artifacts cannot go stale

def test_committed_generated_files_match_the_spec(tmp_path):
    isa.generate_all(PIO16 / "isa.yaml", tmp_path)
    for f in (tmp_path).rglob("*"):
        if f.is_file():
            committed = PIO16 / "gen" / f.relative_to(tmp_path)
            assert committed.exists(), f"{committed} is missing; run `siliconforge isa gen`"
            assert committed.read_text(encoding="utf-8").replace("\r\n", "\n") == f.read_text(encoding="utf-8"), \
                f"{committed} is stale; run `siliconforge isa gen hardware/isa/pio16/isa.yaml --out hardware/isa/pio16/gen`"


# ----------------------------------------------------------------------------- real tools: the worked example

def _native(runner, path: Path) -> str:
    """A host path in the form the runner's own filesystem understands (WSL sees C:/x as /mnt/c/x)."""
    conv = getattr(runner, "to_wsl_path", None)
    return conv(path) if conv else str(path)


def _runner(real_backend):
    from services.orchestrator.config import load_flow_config
    from services.orchestrator.eda.bridge import EDABridge

    cfg = load_flow_config()
    cfg.eda_mode = real_backend
    return EDABridge(cfg).backend.runner


@pytest.mark.real_eda
def test_pio16_rtl_matches_the_golden_model_in_lockstep(real_backend, tmp_path):
    ws = tmp_path / "pio16"
    shutil.copytree(PIO16, ws, ignore=shutil.ignore_patterns("coverage.json", ".siliconforge", "sim_build", "__pycache__"))
    tools = verify.install_tools(ws)
    runner = _runner(real_backend)
    rep = verify.run_cocotb(
        runner, ws, sources=["gen/rtl/pio16_decode.v", "rtl/pio16.v"], top="pio16",
        test_module="test_pio16", test_dir="test", seeds=(1, 2), includes=("gen/rtl",),
        env={"SF_SEEDS": "25", "SF_TOOLS": ".siliconforge/tools", "SF_COVERAGE": _native(runner, ws / "coverage.json")})
    assert rep["ok"], rep["log_tail"]
    assert rep["passed"] == 6 and rep["failed"] == 0 and rep["provenance"] == _runner(real_backend).name
    cov = json.loads((ws / "coverage.json").read_text(encoding="utf-8"))
    assert cov["coverage"] > 0.9, cov["points"]  # randomised programs reach (nearly) every functional bin
    assert tools.exists()


@pytest.mark.real_eda
def test_lockstep_catches_a_planted_rtl_bug(real_backend, tmp_path):
    """The point of lockstep: break the RTL (djnz decrements by two) and the regression must fail, naming the instruction."""
    ws = tmp_path / "pio16"
    shutil.copytree(PIO16, ws, ignore=shutil.ignore_patterns("coverage.json", ".siliconforge", "sim_build", "__pycache__"))
    rtl = (ws / "rtl" / "pio16.v").read_text(encoding="utf-8")
    assert "wire [7:0] x_dec = x - 8'd1;" in rtl
    (ws / "rtl" / "pio16.v").write_text(rtl.replace("x - 8'd1;", "x - 8'd2;"), encoding="utf-8")
    verify.install_tools(ws)
    runner = _runner(real_backend)
    rep = verify.run_cocotb(
        runner, ws, sources=["gen/rtl/pio16_decode.v", "rtl/pio16.v"], top="pio16",
        test_module="test_pio16", test_dir="test", testcase="test_lockstep", includes=("gen/rtl",),
        env={"SF_SEEDS": "25", "SF_TOOLS": ".siliconforge/tools", "SF_COVERAGE": _native(runner, ws / "cov.json")})
    assert not rep["ok"] and rep["failed"] >= 1
    excerpt = "\n".join(rep["failure_excerpts"])
    assert "RTL and model diverge after instruction" in excerpt and "djnz" in excerpt, excerpt  # names the instruction
    assert "x: rtl=" in excerpt  # and shows which architectural register differs
