# SPDX-License-Identifier: AGPL-3.0-or-later
"""The project flow: lint, synthesis with equivalence checking, place and route with signoff gates, on your own RTL.

No model is involved. Every stage runs real tools through a `Runner` (native, WSL or Docker) and the result records which.
A stage that cannot run is an `error` with the reason, never a quiet pass. A stage is `skipped` only if you left it out.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from services.orchestrator.eda.errors import ToolRunError
from services.orchestrator.eda.librelane import LibreLaneFlow
from services.orchestrator.eda.runner import Runner
from services.orchestrator.flow.signoff import Verdict, corner_table, evaluate
from services.orchestrator.project import HDL_SUFFIXES, Project

PASS, FAIL, ERROR, SKIPPED = "pass", "fail", "error", "skipped"
STAGES = ("lint", "synth", "pnr")
SCHEMA = "siliconforge.flow/1"


@dataclass
class StageResult:
    name: str
    status: str
    summary: str
    mode: str
    elapsed_s: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)
    log_tail: str = ""


class ProjectFlow:
    def __init__(self, project: Project, runner: Runner, out_dir: Path, librelane: LibreLaneFlow | None = None,
                 timeouts: dict[str, float] | None = None, waive: set[str] | None = None) -> None:
        self.project, self.runner, self.out = project, runner, Path(out_dir)
        self.librelane = librelane or LibreLaneFlow(runner)
        self.timeouts = {"lint": 300.0, "synth": 1800.0, "pnr": 7200.0, **(timeouts or {})}
        self.waive = waive or set()
        self.out.mkdir(parents=True, exist_ok=True)
        self.metrics: dict[str, Any] = {}
        self.config: dict[str, Any] = {}

    # ---------------------------------------------------------------------------------------- sources

    def stage_sources(self, dest: Path) -> dict[str, Any]:
        """Copy the sources and include directories into `dest/src`, keeping paths relative to the project root, so a tool in
        WSL or a container sees a self-contained tree. Returns the relative file list, include dirs and the sdc path."""
        root = self.project.root
        base = dest / "src"
        if base.exists():
            shutil.rmtree(base)
        base.mkdir(parents=True)

        def place(p: Path) -> str:
            try:
                rel = p.relative_to(root)
            except ValueError:
                rel = Path("_external") / hashlib.sha1(str(p.parent).encode()).hexdigest()[:8] / p.name
            target = base / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
            return (Path("src") / rel).as_posix()

        files = [place(s) for s in self.project.hdl_sources()]
        for s in self.project.sources:  # headers listed as sources
            if s.suffix.lower() in (".vh", ".svh"):
                place(s)
        includes: list[str] = []
        for d in self.project.include_path():
            try:
                rel = d.relative_to(root)
            except ValueError:
                rel = Path("_external") / hashlib.sha1(str(d).encode()).hexdigest()[:8] / d.name
            (base / rel).mkdir(parents=True, exist_ok=True)
            for h in d.iterdir():
                if h.is_file() and h.suffix.lower() in HDL_SUFFIXES and not (base / rel / h.name).exists():
                    shutil.copyfile(h, base / rel / h.name)
            includes.append((Path("src") / rel).as_posix())
        sdc = place(self.project.sdc) if self.project.sdc else None
        return {"files": files, "includes": sorted(set(includes)), "sdc": sdc}

    # ------------------------------------------------------------------------------------------ tools

    def tool_versions(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for tool, cmd in (("verilator", ["verilator", "--version"]), ("yosys", ["yosys", "-V"])):
            res = self.runner.run(cmd, self.out, timeout=60)
            first = (res.stdout or res.stderr).strip().splitlines()
            out[tool] = first[0][:100] if res.ok and first else "not found"
        if self.librelane.available():
            res = self.runner.run([*self.librelane.command, "--version"], self.out, timeout=120, isolate=True)
            out["librelane"] = ((res.stdout or res.stderr).strip().splitlines() or ["unknown"])[0][:100]
        else:
            out["librelane"] = "not found"
        return out

    def _d_args(self, flag: str = "-D") -> list[str]:
        return [f"{flag}{k}={v}" if v != "" else f"{flag}{k}" for k, v in self.project.defines.items()]

    # --------------------------------------------------------------------------------------- stage: lint

    def lint(self) -> StageResult:
        t0 = time.perf_counter()
        work = self.out / "lint"
        work.mkdir(parents=True, exist_ok=True)
        layout = self.stage_sources(work)
        cmd = ["verilator", "--lint-only", "-Wall", "-Wno-fatal", "--top-module", self.project.top,
               *[f"-I{d}" for d in layout["includes"]], *self._d_args(), *layout["files"]]
        res = self.runner.run(cmd, work, timeout=self.timeouts["lint"])
        (work / "verilator.log").write_text(res.log, encoding="utf-8")
        if res.returncode == 127 or "command not found" in res.stderr:
            return StageResult("lint", ERROR, "verilator is not available on this backend", self.runner.name,
                               round(time.perf_counter() - t0, 2), log_tail=res.log[-1500:])
        errors = len(re.findall(r"^%Error", res.log, re.M))
        by_kind: dict[str, int] = {}
        for m in re.finditer(r"^%Warning-([A-Z0-9_]+)", res.log, re.M):
            by_kind[m.group(1)] = by_kind.get(m.group(1), 0) + 1
        warns = sum(by_kind.values())
        status = PASS if errors == 0 and res.ok else FAIL
        summary = f"{errors} error(s), {warns} warning(s)" + (f"; most common: {max(by_kind, key=by_kind.get)}" if by_kind else "")
        return StageResult("lint", status, summary, self.runner.name, round(time.perf_counter() - t0, 2),
                           {"errors": errors, "warnings": warns, "by_kind": by_kind, "files": len(layout["files"])},
                           res.log[-1500:] if status != PASS else "")

    # -------------------------------------------------------------------------------- stage: synth + LEC

    def synth(self, lec: bool = True) -> StageResult:
        t0 = time.perf_counter()
        work = self.out / "synthesis"
        work.mkdir(parents=True, exist_ok=True)
        layout = self.stage_sources(work)
        top = self.project.top
        read = ["read_verilog", "-sv", *[f"-I{d}" for d in layout["includes"]], *self._d_args(), *layout["files"]]
        read_line = " ".join(read)
        # Flatten before the equivalence check: the SAT-based passes have no model for a module instance, so a design with
        # a hierarchy would otherwise fail with "No SAT model available for cell ...". Flattening also makes `stat` report the
        # whole design instead of whichever module it prints first.
        lines = [read_line, f"hierarchy -check -top {top}", "flatten"]
        if lec:
            lines += ["proc", "opt_clean", "async2sync", f"rename {top} gold", "design -stash gold",
                      read_line, f"hierarchy -check -top {top}", "flatten"]
        lines += [f"synth -top {top} -flatten" + (" -nofsm" if lec else ""), "tee -o synth_stat.txt stat",
                  "write_verilog -noattr gate_netlist.v"]
        if lec:
            lines += ["async2sync", f"rename {top} gate", "design -stash gate", "design -copy-from gold -as gold gold",
                      "design -copy-from gate -as gate gate", "equiv_make gold gate equiv", "hierarchy -top equiv",
                      "equiv_simple -undef", "equiv_induct -undef", "equiv_status -assert"]
        (work / "synth.ys").write_text("\n".join(lines) + "\n", encoding="utf-8")
        res = self.runner.run(["yosys", "-q", "-l", "yosys.log", "synth.ys"], work, timeout=self.timeouts["synth"])
        log_file = work / "yosys.log"
        log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else res.log
        if res.returncode == 127:
            return StageResult("synth", ERROR, "yosys is not available on this backend", self.runner.name,
                               round(time.perf_counter() - t0, 2), log_tail=res.log[-1500:])
        if res.returncode == 124:
            return StageResult("synth", ERROR, f"timed out after {self.timeouts['synth']:.0f}s (not counted as a pass)",
                               self.runner.name, round(time.perf_counter() - t0, 2), log_tail=log[-1500:])
        cells = 0
        stat = work / "synth_stat.txt"
        if stat.exists():
            counts = re.findall(r"^\s*(\d+)\s+cells?\b", stat.read_text(encoding="utf-8"), re.M)
            cells = int(counts[-1]) if counts else 0  # the design is flat, so the last count is the whole design
        ok = res.ok
        summary = (f"synthesized to {cells} generic cells; equivalence {'proven' if ok else 'NOT proven'}" if lec
                   else f"synthesized to {cells} generic cells (equivalence check not requested)")
        return StageResult("synth", PASS if ok else FAIL, summary, self.runner.name, round(time.perf_counter() - t0, 2),
                           {"cells": cells, "lec": "proven" if (lec and ok) else ("failed" if lec else "not run")},
                           "" if ok else log[-1800:])

    # ------------------------------------------------------------------------------------- stage: pnr

    def librelane_config(self, layout: dict[str, Any], knobs: dict[str, Any] | None = None) -> dict[str, Any]:
        p = self.project
        cfg: dict[str, Any] = {
            "DESIGN_NAME": p.top,
            "VERILOG_FILES": [f"dir::{f}" for f in layout["files"]],
            "CLOCK_PORT": p.clock_port,
            "CLOCK_PERIOD": p.period_ns,
            "PDK": p.pdk,
        }
        if layout["includes"]:
            cfg["VERILOG_INCLUDE_DIRS"] = [f"dir::{d}" for d in layout["includes"]]
        if p.defines:
            cfg["VERILOG_DEFINES"] = [f"{k}={v}" if v != "" else k for k, v in p.defines.items()]
        if layout["sdc"]:
            cfg["PNR_SDC_FILE"] = f"dir::{layout['sdc']}"
            cfg["SIGNOFF_SDC_FILE"] = f"dir::{layout['sdc']}"
        cfg.update(p.vars)
        cfg.update(knobs or {})
        return cfg

    def pnr(self, knobs: dict[str, Any] | None = None) -> StageResult:
        t0 = time.perf_counter()
        work = self.out / "pnr"
        work.mkdir(parents=True, exist_ok=True)
        layout = self.stage_sources(work)
        self.config = self.librelane_config(layout, knobs)
        if not self.librelane.available():
            return StageResult("pnr", ERROR, "LibreLane is not available on this backend", self.runner.name,
                               round(time.perf_counter() - t0, 2))
        try:
            ppa, metrics = self.librelane.run_config(work, self.config, knobs or {}, self.timeouts["pnr"])
        except ToolRunError as exc:
            return StageResult("pnr", ERROR, "LibreLane did not produce metrics", self.runner.name,
                               round(time.perf_counter() - t0, 2), log_tail=str(exc)[-1800:])
        self.metrics = metrics
        verdict = evaluate(metrics, self.waive)
        status = PASS if verdict.status == "clean" else (FAIL if verdict.status == "failed" else ERROR)
        bad = ", ".join(c.name for c in verdict.failed) or ", ".join(c.name for c in verdict.missing_required)
        summary = f"signoff {verdict.status}" + (f": {bad}" if verdict.status != "clean" else "")
        ppa_dict = asdict(ppa)
        ppa_dict["run_dir"] = self.portable(ppa_dict.get("run_dir"))
        ppa_dict["gds_path"] = self.portable(ppa_dict.get("gds_path"))
        if isinstance(ppa_dict.get("extra"), dict) and ppa_dict["extra"].get("render_png"):
            ppa_dict["extra"]["render_png"] = self.portable(ppa_dict["extra"]["render_png"])
        return StageResult("pnr", status, summary, self.runner.name, round(time.perf_counter() - t0, 2),
                           {"ppa": ppa_dict, "verdict": verdict_dict(verdict), "corners": corner_table(metrics),
                            "gds": ppa_dict["gds_path"]})

    def portable(self, path: str | None) -> str | None:
        """A path relative to the output folder. Reports are meant to be kept, attached and committed, so they must not
        carry the absolute location (and user name) of the machine they ran on."""
        if not path:
            return path
        try:
            return Path(path).resolve().relative_to(self.out.resolve()).as_posix()
        except (ValueError, OSError):
            return Path(path).name

    # --------------------------------------------------------------------------------------------- run

    def run(self, stages: tuple[str, ...] = STAGES, lec: bool = True, knobs: dict[str, Any] | None = None) -> dict[str, Any]:
        t0 = time.perf_counter()
        errs = self.project.validate()
        if errs:
            raise ValueError("project is not valid:\n  " + "\n  ".join(errs))
        unknown = [s for s in stages if s not in STAGES]
        if unknown:
            raise ValueError(f"unknown stage(s) {unknown}; choose from {STAGES}")
        results: list[StageResult] = []
        for name in STAGES:
            if name not in stages:
                results.append(StageResult(name, SKIPPED, "not requested", self.runner.name))
                continue
            r = {"lint": self.lint, "synth": lambda: self.synth(lec), "pnr": lambda: self.pnr(knobs)}[name]()
            results.append(r)
        return build_report(self, results, time.perf_counter() - t0, self.tool_versions())


def verdict_dict(v: Verdict) -> dict[str, Any]:
    return {"status": v.status, "checks": [asdict(c) for c in v.checks]}


def build_report(flow: ProjectFlow, results: list[StageResult], elapsed: float, tools: dict[str, str]) -> dict[str, Any]:
    p = flow.project
    pnr = next((r for r in results if r.name == "pnr" and r.data), None)
    ran = [r for r in results if r.status != SKIPPED]
    if any(r.status == FAIL for r in ran):
        overall = "failed"
    elif any(r.status == ERROR for r in ran):
        overall = "incomplete"
    elif not ran:
        overall = "nothing run"
    else:
        overall = "clean"
    return {
        "schema": SCHEMA,
        "project": {"name": p.name, "top": p.top, "pdk": p.pdk, "clock": {"port": p.clock_port, "period_ns": p.period_ns},
                    "sources": len(p.sources), "defines": p.defines, "fingerprint": p.fingerprint(), "sdc": bool(p.sdc)},
        "runner": flow.runner.name,
        "tools": tools,
        "stages": [asdict(r) for r in results],
        "overall": overall,
        "ppa": pnr.data.get("ppa") if pnr else None,
        "verdict": pnr.data.get("verdict") if pnr else None,
        "corners": pnr.data.get("corners") if pnr else [],
        "librelane_config": flow.config,
        "config_sha256": hashlib.sha256(json.dumps(flow.config, sort_keys=True).encode()).hexdigest() if flow.config else None,
        "elapsed_s": round(elapsed, 1),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
