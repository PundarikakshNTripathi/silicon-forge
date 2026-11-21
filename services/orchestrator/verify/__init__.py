"""Verification toolkit: mutation testing, functional and toggle coverage, protocol decoding, cocotb regression.

The heavy lifting is done by standard-library-only scripts in `verify/tools/` that run wherever the EDA tools run
(native, WSL or Docker) through the same `Runner` the rest of SiliconForge uses. They are copied into the workspace
(`.siliconforge/tools/`) before running, so the exact version that produced a result sits beside it, and a project can
vendor them into its own CI. This module is the thin Windows/Linux-side wrapper that launches them and reads back JSON.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).parent / "tools"
SCRIPTS = ("sf_mutate.py", "sf_cocotb_run.py", "sf_toggle.py", "sf_protocols.py", "sf_lockstep.py", "sf_tt_synth.py")


class VerifyError(RuntimeError):
    pass


def install_tools(workspace: Path) -> Path:
    """Copy the tool scripts into `<workspace>/.siliconforge/tools` and return that directory."""
    dest = Path(workspace) / ".siliconforge" / "tools"
    dest.mkdir(parents=True, exist_ok=True)
    for name in SCRIPTS:
        src = TOOLS_DIR / name
        if src.exists():
            shutil.copyfile(src, dest / name)
    return dest


def run_tool(runner: Any, workspace: Path, script: str, args: list[str], timeout: float = 3600.0,
             python: str = "python3") -> Any:
    """Run one tool script through `runner` with the workspace as its working directory."""
    install_tools(workspace)
    return runner.run([python, f".siliconforge/tools/{script}", *args], Path(workspace), timeout=timeout)


def mutation_test(runner: Any, workspace: Path, files: list[str], test_cmd: str, top: str | None = None,
                  max_mutants: int = 60, seed: int = 1, jobs: int = 4, out: str = ".siliconforge/mutation.json",
                  timeout: float = 7200.0) -> dict[str, Any]:
    """Score a test suite by planting bugs. Returns the parsed report, with `provenance` set to the runner name."""
    args = ["--root", ".", "--test", test_cmd, "--max", str(max_mutants), "--seed", str(seed), "--jobs", str(jobs),
            "--out", out]
    for f in files:
        args += ["--file", f]
    if top:
        args += ["--top", top]
    res = run_tool(runner, workspace, "sf_mutate.py", args, timeout)
    report_path = Path(workspace) / out
    if not report_path.exists():
        raise VerifyError(f"mutation run produced no report (exit {res.returncode}): {res.log[-600:]}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["provenance"] = runner.name
    report["command"] = " ".join(shlex.quote(a) for a in args)
    if "error" in report:
        raise VerifyError(report["error"])
    return report


def failure_excerpts(log: str, limit: int = 6) -> list[str]:
    """The text cocotb logged when a test failed (its XML message is often empty): the warning block and traceback."""
    out: list[str] = []
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if "WARNING" in line and "cocotb.task.Test" in line:
            block = [line.split("WARNING", 1)[1].strip()]
            for follow in lines[i + 1:i + 40]:
                if re.match(r"\s*\d+\.\d+\s*ns\s+(INFO|WARNING|ERROR)", follow):
                    break
                block.append(follow.strip())
            out.append(chr(10).join(b for b in block if b)[:1500])
            if len(out) >= limit:
                break
    return out


def _read_json(path: Path, what: str, res: Any) -> dict[str, Any]:
    if not path.exists():
        raise VerifyError(f"{what} produced no report (exit {res.returncode}): {res.log[-600:]}")
    return json.loads(path.read_text(encoding="utf-8"))


def run_cocotb(runner: Any, workspace: Path, *, sources: list[str], top: str, test_module: str, test_dir: str = ".",
               seeds: tuple[int, ...] = (1,), includes: tuple[str, ...] = (), defines: dict[str, str] | None = None,
               env: dict[str, str] | None = None, waves: bool = False, testcase: str | None = None,
               out: str = ".siliconforge/sim.json", build_dir: str = ".siliconforge/sim_build",
               timeout: float = 3600.0) -> dict[str, Any]:
    """Run a cocotb test module with Icarus. The result carries the runner's provenance; a failing test is data
    (`ok: False`), not an exception, so callers can report it."""
    args = ["--top", top, "--test-module", test_module, "--test-dir", test_dir, "--seeds", ",".join(map(str, seeds)),
            "--build-dir", build_dir, "--out", out, "--sources", *sources]
    for i in includes:
        args += ["--include", i]
    for k, v in (defines or {}).items():
        args += ["--define", f"{k}={v}"]
    for k, v in (env or {}).items():
        args += ["--env", f"{k}={v}"]
    if waves:
        args.append("--waves")
    if testcase:
        args += ["--testcase", testcase]
    Path(workspace, out).unlink(missing_ok=True)
    res = run_tool(runner, workspace, "sf_cocotb_run.py", args, timeout)
    report = _read_json(Path(workspace) / out, "cocotb run", res)
    if "error" in report:
        raise VerifyError(report["error"])
    report["provenance"] = runner.name
    report["log_tail"] = res.log[-1500:]
    report["failure_excerpts"] = failure_excerpts(res.log)
    return report


def toggle_coverage(runner: Any, workspace: Path, dump: str, scope: str = "", exclude: tuple[str, ...] = (),
                    out: str = ".siliconforge/toggle.json") -> dict[str, Any]:
    args = [dump, "--out", out] + (["--scope", scope] if scope else [])
    for e in exclude:
        args += ["--exclude", e]
    res = run_tool(runner, workspace, "sf_toggle.py", args, 600.0)
    report = _read_json(Path(workspace) / out, "toggle coverage", res)
    report["provenance"] = runner.name
    return report
