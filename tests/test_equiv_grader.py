# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pairwise equivalence grader: script, verdict parsing with a fake runner, and the self-check on real Yosys."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.orchestrator.eda.runner import RunResult
from services.orchestrator.evals.equiv import check_equivalent, equivalence_script, self_check

COUNTER = """module cnt (input logic clk, input logic rst_n, input logic en, output logic [3:0] q);
  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) q <= 4'd0; else if (en) q <= q + 4'd1;
endmodule
"""
COUNTER_RESTYLED = COUNTER.replace("q <= q + 4'd1", "q <= 4'd1 + q")  # same behaviour, different text
MUTANT = COUNTER.replace("q + 4'd1", "q + 4'd2")


class FakeRunner:
    name = "fake"

    def __init__(self, returncode=0, stdout="", has=True):
        self.rc, self.out, self.has = returncode, stdout, has

    def has_tool(self, tool):
        return self.has

    def run(self, cmd, cwd, timeout=0.0, **kw):
        return RunResult(self.rc, self.out, "", 0.01)


def test_script_compares_flattened_designs_with_the_notsotiny_recipe():
    s = equivalence_script("cnt", 30)
    assert "equiv_make gold cand equiv" in s and "equiv_simple" in s and "equiv_induct -seq 30" in s
    assert s.rstrip().endswith("equiv_status -assert") and s.count("flatten") == 2


def test_verdicts_come_from_the_tool_result_and_say_what_they_do_not_know(tmp_path):
    ok = check_equivalent(FakeRunner(0), tmp_path, COUNTER, COUNTER, "cnt")
    assert ok.equivalent and ok.mode == "fake"
    un = check_equivalent(FakeRunner(1, "ERROR: Found 2 unproven $equiv cells."), tmp_path / "b", COUNTER, MUTANT, "cnt")
    assert un.verdict == "not_proven" and un.unproven == 2 and "different" not in un.detail
    err = check_equivalent(FakeRunner(1, "ERROR: syntax error near end of file"), tmp_path / "c", COUNTER, "module", "cnt")
    assert err.verdict == "error" and "syntax" in err.detail
    assert check_equivalent(FakeRunner(has=False), tmp_path / "d", COUNTER, COUNTER, "cnt").verdict == "error"


def test_a_module_name_cannot_inject_script_lines(tmp_path):
    with pytest.raises(ValueError):
        check_equivalent(FakeRunner(), tmp_path, COUNTER, COUNTER, "cnt; shell rm -rf /")


def _real_runner():
    import shutil

    from services.orchestrator.eda.runner import NativeRunner, WslRunner

    if shutil.which("yosys"):
        return NativeRunner()
    if shutil.which("wsl"):
        r = WslRunner()
        try:
            if r.has_tool("yosys"):
                return r
        except OSError:
            pass
    pytest.skip("yosys is not available natively or in WSL")


@pytest.mark.real_eda
def test_real_yosys_proves_a_restyled_counter_and_rejects_a_mutant(tmp_path: Path):
    runner = _real_runner()
    assert check_equivalent(runner, tmp_path / "a", COUNTER, COUNTER_RESTYLED, "cnt").verdict == "equivalent"
    sc = self_check(runner, tmp_path / "b", COUNTER, MUTANT, "cnt")
    assert sc["ok"], sc
    assert sc["mode"] in ("native", "wsl", "docker")
