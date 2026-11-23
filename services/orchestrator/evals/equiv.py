# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pairwise RTL equivalence: is a candidate module the same as a reference, proven by Yosys?

This is the grading recipe of NotSoTiny (arXiv 2512.20823): build a miter of reference and candidate (`equiv_make`), close what is
combinational with `equiv_simple`, then try sequential induction with `equiv_induct -seq K` (K = 30 there). It needs no testbench, so
a candidate cannot pass by satisfying a weak one; it does not call a model either.

What the verdict means, and does not:

* `equivalent`: every compared signal pair was proven. The proof is bounded induction, so it is a proof only within what induction
  closes at depth K; a reset that differs but converges inside K cycles can still pass.
* `not_proven`: at least one pair was not proven. That is a *different* design or one induction could not close: this tool does not
  tell the two apart and says so instead of calling it "different".
* `error`: the tools failed before a verdict (syntax error, interface mismatch, missing tool); `detail` has the reason.

A grader must be checked before it scores anything: `self_check` runs reference-vs-reference (must be `equivalent`) and
reference-vs-mutant (must not be). Provenance (`mode`) is the runner's name, as everywhere else in this repository.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from services.orchestrator.eda.runner import Runner

DEFAULT_K = 30
_UNPROVEN = re.compile(r"Found (\d+) unproven \$equiv cells?")


@dataclass
class EquivResult:
    verdict: str  # equivalent | not_proven | error
    mode: str  # which runner produced it: native | wsl | docker
    unproven: int | None
    detail: str
    elapsed_sec: float

    @property
    def equivalent(self) -> bool:
        return self.verdict == "equivalent"


def equivalence_script(top: str, k: int = DEFAULT_K) -> str:
    """The Yosys script. Both designs are flattened to the same module name, renamed gold and cand, then compared."""
    def side(file: str, name: str) -> list[str]:
        return [f"read_verilog -sv {file}", f"hierarchy -check -top {top}", "proc", "flatten", "opt_clean", "async2sync",
                f"rename {top} {name}", f"design -stash {name}"]
    return "\n".join([
        *side("gold.sv", "gold"), *side("cand.sv", "cand"),
        "design -copy-from gold -as gold gold", "design -copy-from cand -as cand cand",
        "equiv_make gold cand equiv", "hierarchy -top equiv", "equiv_simple", f"equiv_induct -seq {int(k)}", "equiv_status -assert",
    ]) + "\n"


def check_equivalent(runner: Runner, workdir: Path, golden: str, candidate: str, top: str, *, k: int = DEFAULT_K,
                     timeout: float = 300.0) -> EquivResult:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top):
        raise ValueError(f"not a module name: {top!r}")
    if not runner.has_tool("yosys"):
        return EquivResult("error", runner.name, None, "yosys is not available on this backend", 0.0)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "gold.sv").write_text(golden, encoding="utf-8")
    (workdir / "cand.sv").write_text(candidate, encoding="utf-8")
    (workdir / "equiv.ys").write_text(equivalence_script(top, k), encoding="utf-8")
    res = runner.run(["yosys", "-q", "-l", "yosys.log", "equiv.ys"], workdir, timeout=timeout)
    log_file = workdir / "yosys.log"
    log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else res.log
    if res.ok:
        return EquivResult("equivalent", runner.name, 0, "all compared signals proven", res.elapsed_sec)
    m = _UNPROVEN.search(log) or _UNPROVEN.search(res.log)
    if m:
        return EquivResult("not_proven", runner.name, int(m.group(1)), f"{m.group(1)} signal pair(s) not proven", res.elapsed_sec)
    text = res.log or log
    reason = next((ln for ln in reversed(text.splitlines()) if "ERROR" in ln), text[-300:])
    return EquivResult("error", runner.name, None, reason.strip(), res.elapsed_sec)


def self_check(runner: Runner, workdir: Path, golden: str, mutant: str, top: str, **kw) -> dict:
    """Check the grader itself. `ok` is true only if the reference equals itself and the mutant is not proven equal."""
    same = check_equivalent(runner, workdir / "self", golden, golden, top, **kw)
    diff = check_equivalent(runner, workdir / "mutant", golden, mutant, top, **kw)
    return {"ok": same.verdict == "equivalent" and diff.verdict == "not_proven", "reference_vs_reference": same.verdict,
            "reference_vs_mutant": diff.verdict, "mode": same.mode}
