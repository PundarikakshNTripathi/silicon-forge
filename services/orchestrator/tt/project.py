# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tiny Tapeout project support (IHP SG13CMOS5L): info.yaml validation, tile budget, scaffold, preflight, submission pack.

What this does NOT do: it is not the official precheck. Tiny Tapeout's own GDS action (hardening) and precheck (DRC and
friends) decide whether a design is accepted; the checks here catch the common mistakes earlier, on your machine, with
the real PDK where one is available, and say plainly what they could not check.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PDK = "ihp-sg13cmos5l"
YAML_VERSION = 6
#: die size in um for each tile configuration (TinyTapeout/tt-support-tools, tech/ihp-sg13cmos5l/tile_sizes.yaml)
TILE_SIZES_UM: dict[str, tuple[float, float]] = {
    "1x1": (202.08, 154.98), "1x2": (202.08, 313.74), "2x2": (419.52, 313.74), "3x2": (636.96, 313.74),
    "3x4": (636.96, 710.64), "4x2": (854.40, 313.74), "4x4": (854.40, 710.64), "5x4": (1071.84, 710.64),
    "6x2": (1289.28, 313.74), "6x4": (1289.28, 710.64), "8x2": (1724.16, 313.74), "8x4": (1724.16, 710.64),
}
REQUIRED_PORTS = ("ui_in", "uo_out", "uio_in", "uio_out", "uio_oe", "ena", "clk", "rst_n")
PIN_KEYS = [f"{p}[{i}]" for p in ("ui", "uo", "uio") for i in range(8)]
#: community entries closed timing and routed at roughly this utilization; Tiny Tapeout's default placement density is 60%
UTIL_COMFORTABLE, UTIL_TIGHT = 0.50, 0.65


def die_area_um2(tiles: str) -> float:
    w, h = TILE_SIZES_UM[tiles]
    return w * h


# ------------------------------------------------------------------------------------------------ results

@dataclass
class Check:
    name: str
    status: str  # pass | warn | fail | info
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(name, status, detail))

    @property
    def ok(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)

    def text(self) -> str:
        mark = {"pass": "ok  ", "warn": "WARN", "fail": "FAIL", "info": "info"}
        return "\n".join(f"{mark[c.status]} {c.name}" + (f": {c.detail}" if c.detail else "") for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [c.__dict__ for c in self.checks]}


# --------------------------------------------------------------------------------------------------- info.yaml

def load_info(root: Path) -> dict[str, Any]:
    f = Path(root) / "info.yaml"
    if not f.is_file():
        raise FileNotFoundError(f"{f} not found: is this a Tiny Tapeout project folder?")
    return yaml.safe_load(f.read_text(encoding="utf-8")) or {}


def check_info(root: Path, info: dict[str, Any]) -> Report:
    rep = Report()
    p = info.get("project") or {}
    for key in ("title", "author", "description"):
        rep.add(f"info.yaml project.{key}", "pass" if str(p.get(key, "")).strip() else "fail",
                "" if str(p.get(key, "")).strip() else "required, currently empty")
    top = str(p.get("top_module", ""))
    rep.add("top_module name", "pass" if top.startswith("tt_um_") else "fail",
            f"'{top}' must start with tt_um_" if not top.startswith("tt_um_") else top)
    tiles = str(p.get("tiles", ""))
    rep.add("tiles", "pass" if tiles in TILE_SIZES_UM else "fail",
            tiles if tiles in TILE_SIZES_UM else f"'{tiles}' is not one of {', '.join(TILE_SIZES_UM)}")
    clock = p.get("clock_hz", 0)
    rep.add("clock_hz", "pass" if isinstance(clock, int) and clock > 0 else "warn",
            f"{clock} Hz" if clock else "0: the datasheet will not state a clock frequency")
    missing = [s for s in p.get("source_files", []) if not (Path(root) / "src" / s).is_file()]
    rep.add("source_files exist in src/", "fail" if missing or not p.get("source_files") else "pass",
            f"missing: {', '.join(missing)}" if missing else ("none listed" if not p.get("source_files") else ""))
    pinout = info.get("pinout") or {}
    absent = [k for k in PIN_KEYS if k not in pinout]
    rep.add("pinout has all 24 pins", "fail" if absent else "pass", f"missing: {', '.join(absent[:6])}" if absent else "")
    rep.add("pinout described", "pass" if any(str(v).strip() for v in pinout.values()) else "warn",
            "" if any(str(v).strip() for v in pinout.values()) else "every pin name is empty (the datasheet will be blank)")
    rep.add("yaml_version", "pass" if info.get("yaml_version") == YAML_VERSION else "warn",
            f"{info.get('yaml_version')} (the template uses {YAML_VERSION})" if info.get("yaml_version") != YAML_VERSION else "")
    return rep


# ----------------------------------------------------------------------------------------------- source checks

def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def check_sources(root: Path, info: dict[str, Any]) -> Report:
    rep = Report()
    p = info.get("project") or {}
    top = str(p.get("top_module", ""))
    texts = {}
    for s in p.get("source_files", []):
        f = Path(root) / "src" / s
        if f.is_file():
            texts[s] = _strip_comments(f.read_text(encoding="utf-8", errors="replace"))
    allsrc = "\n".join(texts.values())
    m = re.search(rf"\bmodule\s+{re.escape(top)}\b(.*?)\)\s*;", allsrc, flags=re.S) if top else None
    if not m:
        rep.add("top module found", "fail", f"no `module {top}` in the listed source files")
        return rep
    rep.add("top module found", "pass", top)
    header = m.group(1)
    absent = [port for port in REQUIRED_PORTS if not re.search(rf"\b{port}\b", header)]
    rep.add("required ports", "fail" if absent else "pass",
            f"missing: {', '.join(absent)}" if absent else "ui_in uo_out uio_in uio_out uio_oe ena clk rst_n")
    body = allsrc[m.end():]
    uses_reset = bool(re.search(r"\brst_n\b", body))
    rep.add("reset is used", "pass" if uses_reset else "warn",
            "" if uses_reset else "rst_n is declared but never used; state will be unknown at power-up")
    driven = [o for o in ("uo_out", "uio_out", "uio_oe") if re.search(rf"\b{o}\b\s*(?:\[[^\]]*\]\s*)?(?:<=|=)", body)
              or re.search(rf"\.\w+\s*\(\s*{o}\b", body) or re.search(rf"assign\s+{o}\b", body)]
    rep.add("outputs driven", "pass" if len(driven) == 3 else "warn",
            "" if len(driven) == 3 else f"no assignment seen for {', '.join(set(('uo_out', 'uio_out', 'uio_oe')) - set(driven))}")
    if re.search(r"\binitial\b", allsrc):
        rep.add("no initial blocks", "warn", "`initial` is simulation-only; the chip will not run it")
    if re.search(r"(?<![\w'])#\s*\d", allsrc):
        rep.add("no delays", "warn", "`#` delays are ignored by synthesis")
    if not re.search(r"`default_nettype\s+none", "\n".join(Path(root, "src", s).read_text(encoding="utf-8", errors="replace")
                                                             for s in texts)):
        rep.add("default_nettype none", "info", "recommended: catches misspelled signal names")
    return rep


# ------------------------------------------------------------------------------------------------------- budget

def budget(synth_area_um2: float, tiles: str | None = None, margin: float = 1.2) -> dict[str, Any]:
    """How a post-synthesis area maps onto tile sizes. `margin` stands in for clock tree, hold buffers and repair that
    layout adds on top of synthesis; it is an assumption, so measure after hardening."""
    need = synth_area_um2 * margin
    rows = []
    for name in TILE_SIZES_UM:
        util = need / die_area_um2(name)
        rows.append({"tiles": name, "die_um2": round(die_area_um2(name)), "utilization": round(util, 3),
                     "verdict": "comfortable" if util <= UTIL_COMFORTABLE else "tight" if util <= UTIL_TIGHT else "too small"})
    smallest = next((r for r in sorted(rows, key=lambda r: r["die_um2"]) if r["verdict"] != "too small"), None)
    out = {"synth_area_um2": round(synth_area_um2, 1), "margin": margin, "assumed_extra": "clock tree, hold fix, repair",
           "options": rows, "smallest_that_fits": smallest and smallest["tiles"]}
    if tiles:
        out["chosen"] = next(r for r in rows if r["tiles"] == tiles)
    return out


# ------------------------------------------------------------------------------------------------------ scaffold

_CONFIG = {
    "PL_TARGET_DENSITY_PCT": 60, "CLOCK_PERIOD": 20, "PL_RESIZER_HOLD_SLACK_MARGIN": 0.1,
    "GRT_RESIZER_HOLD_SLACK_MARGIN": 0.05, "RUN_LINTER": 1, "LINTER_INCLUDE_PDK_MODELS": 1, "CLOCK_PORT": "clk",
    "RUN_KLAYOUT_XOR": 0, "RUN_KLAYOUT_DRC": 0, "DESIGN_REPAIR_BUFFER_OUTPUT_PORTS": 0, "TOP_MARGIN_MULT": 1,
    "BOTTOM_MARGIN_MULT": 1, "LEFT_MARGIN_MULT": 6, "RIGHT_MARGIN_MULT": 6, "FP_SIZING": "absolute",
    "GRT_ALLOW_CONGESTION": 1, "FP_IO_HLENGTH": 2, "FP_IO_VLENGTH": 2, "FP_PDN_VPITCH": 50.0, "FP_PDN_VWIDTH": 2.1,
    "RUN_CTS": 1, "FP_PDN_MULTILAYER": 0, "MAGIC_DEF_LABELS": 0, "MAGIC_WRITE_LEF_PINONLY": 1,
}

_PROJECT_V = """\
/*
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module {top} (
    input  wire [7:0] ui_in,    // dedicated inputs
    output wire [7:0] uo_out,   // dedicated outputs
    input  wire [7:0] uio_in,   // bidirectional pins: input path
    output wire [7:0] uio_out,  // bidirectional pins: output path
    output wire [7:0] uio_oe,   // bidirectional pins: output enable (1 = drive)
    input  wire       ena,      // high while the design is selected
    input  wire       clk,
    input  wire       rst_n     // active-low reset
);

  // Every output must be driven. Replace this with your design.
  assign uo_out  = ui_in;
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;

  wire _unused = &{{ena, clk, rst_n, uio_in, 1'b0}};

endmodule
"""

_TB_V = """\
`default_nettype none
`timescale 1ns / 1ps

module tb ();
  initial begin
    $dumpfile("tb.fst");
    $dumpvars(0, tb);
    #1;
  end

  reg clk;
  reg rst_n;
  reg ena;
  reg [7:0] ui_in;
  reg [7:0] uio_in;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  {top} user_project (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );
endmodule
"""

_TEST_PY = """\
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles


@cocotb.test()
async def test_project(dut):
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    dut.ui_in.value = 0xA5
    await ClockCycles(dut.clk, 2)
    assert dut.uo_out.value == 0xA5
"""

_INFO_MD = """\
## How it works

## How to test

## External hardware
"""

_PINS = ("ui", "uo", "uio")


def scaffold(root: Path, top: str, *, title: str = "", author: str = "", description: str = "", tiles: str = "1x1",
             clock_hz: int = 50_000_000, language: str = "Verilog", force: bool = False) -> list[Path]:
    """Write a Tiny Tapeout IHP project skeleton that follows the official template's layout."""
    if not top.startswith("tt_um_"):
        raise ValueError("top module must start with tt_um_")
    if tiles not in TILE_SIZES_UM:
        raise ValueError(f"tiles must be one of {', '.join(TILE_SIZES_UM)}")
    root = Path(root)
    if (root / "info.yaml").exists() and not force:
        raise FileExistsError(f"{root / 'info.yaml'} exists (pass force to overwrite)")
    info = {
        "project": {"title": title, "author": author, "discord": "", "description": description, "language": language,
                    "clock_hz": clock_hz, "tiles": tiles, "top_module": top, "source_files": ["project.v"]},
        "pinout": {f"{p}[{i}]": "" for p in _PINS for i in range(8)},
        "yaml_version": YAML_VERSION,
    }
    files = {
        "info.yaml": yaml.safe_dump(info, sort_keys=False, width=120),
        "src/project.v": _PROJECT_V.format(top=top),
        "src/config.json": json.dumps(_CONFIG, indent=2) + "\n",
        "test/tb.v": _TB_V.format(top=top),
        "test/test.py": _TEST_PY,
        "test/requirements.txt": "pytest==8.4.2\ncocotb==2.0.1\n",
        "docs/info.md": _INFO_MD,
        ".gitignore": "sim_build/\n__pycache__/\n*.fst\n*.vcd\nresults.xml\nrunail/\nruns/\n.siliconforge/\n",
        ".github/workflows/gds.yaml": (
            "name: gds\non: [push, workflow_dispatch]\njobs:\n  gds:\n    runs-on: ubuntu-24.04\n    steps:\n"
            "      - uses: actions/checkout@v6\n        with:\n          submodules: recursive\n"
            f"      - name: Build GDS\n        uses: TinyTapeout/tt-gds-action@ttihp27a\n        with:\n          pdk: {PDK}\n"
            "  precheck:\n    needs: gds\n    runs-on: ubuntu-24.04\n    steps:\n"
            "      - name: Run Tiny Tapeout precheck\n        uses: TinyTapeout/tt-gds-action/precheck@ttihp27a\n"
            "  gl_test:\n    needs: gds\n    runs-on: ubuntu-24.04\n    steps:\n"
            "      - uses: actions/checkout@v6\n        with:\n          submodules: recursive\n"
            "      - name: Gate-level test\n        uses: TinyTapeout/tt-gds-action/gl_test@ttihp27a\n"),
    }
    written = []
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


# --------------------------------------------------------------------------------------------------- preflight

def preflight(root: Path, synth: dict[str, Any] | None = None, harden: dict[str, Any] | None = None) -> Report:
    """Everything checkable without the official flow, in one report. Pass a synth report to include area/latch checks."""
    root = Path(root)
    try:
        info = load_info(root)
    except FileNotFoundError as exc:
        rep = Report()
        rep.add("info.yaml", "fail", str(exc))
        return rep
    rep = check_info(root, info)
    rep.checks += check_sources(root, info).checks
    tiles = str((info.get("project") or {}).get("tiles", ""))
    if synth and synth.get("ok") and synth.get("area_um2") is not None:
        b = budget(float(synth["area_um2"]), tiles if tiles in TILE_SIZES_UM else None)
        ch = b.get("chosen")
        if ch:
            rep.add("area vs tiles (post-synthesis)", "pass" if ch["verdict"] == "comfortable" else
                    "warn" if ch["verdict"] == "tight" else "fail",
                    f"{synth['area_um2']:.0f} um2 x{b['margin']} margin = {ch['utilization']:.0%} of {tiles} ({ch['verdict']}); "
                    f"smallest that fits: {b['smallest_that_fits']}")
        rep.add("latches", "pass" if synth.get("latches", 0) == 0 else "warn",
                f"{synth.get('latches', 0)} latch cell(s)" + ("" if synth.get("latches", 0) == 0 else
                                                              ": intended? unintended latches usually mean an incomplete if/case"))
        if synth["area_um2"] > 0:
            rep.add("sequential area share", "info",
                    f"{100 * synth['sequential_area_um2'] / synth['area_um2']:.0f}% of the area is flip-flops/latches")
        else:
            rep.add("design is empty", "warn", "synthesis produced no cells: the outputs are wired straight to inputs or constants")
    else:
        rep.add("synthesis", "info", "not run: pass a synthesis report to check area and latches against the tile budget")
    if harden:
        rep.add("hardening", "pass" if harden.get("ok") else "fail", harden.get("summary", ""))
    else:
        rep.add("timing, DRC, LVS", "info", "needs the official flow (siliconforge tt harden, or push to run tt-gds-action); not checked here")
    return rep


# ------------------------------------------------------------------------------------------------------ pack

def submission_checklist(root: Path, info: dict[str, Any]) -> str:
    p = info.get("project") or {}
    return "\n".join([
        f"# Submission checklist: {p.get('title') or p.get('top_module')}", "",
        "Nothing here is submitted for you. Each step is yours.", "",
        "- [ ] `info.yaml`: title, author, description, clock_hz and all 24 pin names are filled in",
        "- [ ] `docs/info.md`: how it works, how to test, external hardware",
        "- [ ] The project is in a public GitHub repository with an open-source licence file",
        "- [ ] The `gds` workflow is green (hardening) and the precheck and gate-level jobs pass",
        "- [ ] Timing met in the slow corner at the chosen clock (see the hardening report)",
        "- [ ] AI-use disclosure written (`siliconforge report ai-usage`, then the human-written section completed)",
        "- [ ] Verification write-up: what was proven, what was simulated, coverage, mutation score, what is not covered",
        "- [ ] Submit through the official Tiny Tapeout / Jane Street channel before the deadline",
        "",
    ])
