"""Tiny Tapeout (IHP SG13CMOS5L) support: scaffold, preflight, tile budget, synthesis, gate-level simulation, hardening."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from services.orchestrator import verify
from services.orchestrator.tt.project import (
    PDK,
    TILE_SIZES_UM,
    Check,
    Report,
    budget,
    check_info,
    check_sources,
    die_area_um2,
    load_info,
    preflight,
    scaffold,
    submission_checklist,
)

#: what the official GDS action pins for this PDK (TinyTapeout/tt-gds-action @ttihp27a); hardening should match it
LIBRELANE_VERSION = "3.1.0.dev4"
PDK_VERSION = "89d42ee6d2fd9b50b86a1aada6fee6673c3e6008"
TT_TOOLS_REPO = "https://github.com/TinyTapeout/tt-support-tools.git"


def synthesize(runner: Any, root: Path, *, tiles_info: dict[str, Any] | None = None, sources: list[str] | None = None,
               top: str | None = None, include_dir: str = "src", corner: str = "typ_1p20V_25C",
               out: str = ".siliconforge/tt") -> dict[str, Any]:
    """Synthesize the project against the real IHP liberty. Defaults come from info.yaml."""
    root = Path(root)
    info = tiles_info or load_info(root)
    proj = info.get("project") or {}
    srcs = sources or [f"src/{s}" for s in proj.get("source_files", [])]
    top = top or str(proj.get("top_module", ""))
    args = ["--top", top, "--pdk", PDK, "--corner", corner, "--out", out, "--include", include_dir, "--sources", *srcs]
    res = verify.run_tool(runner, root, "sf_tt_synth.py", args, 1800.0)
    report_path = root / out / "synth.json"
    if not report_path.exists():
        raise verify.VerifyError(f"synthesis produced no report (exit {res.returncode}): {res.log[-600:]}")
    rep = json.loads(report_path.read_text(encoding="utf-8"))
    rep["provenance"] = runner.name
    if not rep.get("ok"):
        raise verify.VerifyError("synthesis failed: " + "; ".join(rep.get("detail", [])))
    return rep


def run_gls(runner: Any, root: Path, *, netlist: str = ".siliconforge/tt/netlist.v", top_tb: str = "tb",
            test_module: str = "test", test_dir: str = "test", tb: str = "test/tb.v", seeds: tuple[int, ...] = (1,),
            corner_note: str = "functional (zero-delay)") -> dict[str, Any]:
    """Run the project's cocotb tests on a gate-level netlist with the PDK's cell models.

    The netlist is our own post-synthesis netlist, not the hardened one: it catches synthesis mismatches, X/reset problems
    and anything the RTL simulator hides, but the final netlist from hardening still needs the official gl_test."""
    fam = PDK.replace("ihp-", "")
    lib = f"$PDK_ROOT/{PDK}/libs.ref/{fam}_stdcell/verilog"
    rep = verify.run_cocotb(
        runner, Path(root), sources=[f"{lib}/{fam}_udp.v", f"{lib}/{fam}_stdcell.v", netlist, tb], top=top_tb,
        test_module=test_module, test_dir=test_dir, seeds=seeds, includes=("src",),
        defines={"GL_TEST": "1", "FUNCTIONAL": "1", "SIM": "1"}, build_dir=".siliconforge/sim_build_gl",
        out=".siliconforge/gls.json")
    rep["netlist_kind"] = "post-synthesis netlist from SiliconForge, " + corner_note
    return rep


def docker_ready(timeout: float = 15.0) -> tuple[bool, str]:
    """Is a Docker engine answering? Official hardening runs LibreLane in a container."""
    exe = shutil.which("docker")
    if exe is None:
        return False, "docker is not installed or not on PATH"
    try:
        p = subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False, "docker did not answer (is Docker Desktop running?)"
    if p.returncode != 0:
        return False, "the Docker engine is not running: start Docker Desktop and try again"
    return True, p.stdout.strip()


__all__ = [
    "LIBRELANE_VERSION", "PDK", "PDK_VERSION", "TILE_SIZES_UM", "TT_TOOLS_REPO", "Check", "Report", "budget",
    "check_info", "check_sources", "die_area_um2", "docker_ready", "load_info", "preflight", "run_gls", "scaffold",
    "submission_checklist", "synthesize",
]
