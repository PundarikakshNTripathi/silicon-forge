# SPDX-License-Identifier: AGPL-3.0-or-later
"""Evaluation harness: statistics, dataset fetch, grading, arms and the result file. No network, no live model."""
import io
import json
import math
import zipfile
from pathlib import Path

import pytest

from services.orchestrator.evals import verilogeval as ve
from services.orchestrator.evals.arms import LLMReply, build_arm
from services.orchestrator.evals.runner import SCHEMA, load_result, run_verilogeval, write_result
from services.orchestrator.evals.stats import bootstrap_ci, paired_difference, pass_at_k, summarize

# ----------------------------------------------------------------------------------------------- statistics


def test_pass_at_k_matches_the_closed_form():
    assert pass_at_k(10, 0, 1) == 0.0 and pass_at_k(10, 10, 1) == 1.0
    assert pass_at_k(10, 3, 1) == pytest.approx(0.3)
    # n=5, c=2, k=2: 1 - C(3,2)/C(5,2) = 1 - 3/10
    assert pass_at_k(5, 2, 2) == pytest.approx(0.7)
    assert pass_at_k(5, 2, 2) == pytest.approx(1 - math.comb(3, 2) / math.comb(5, 2))
    # fewer failures than k means every draw of k contains a pass
    assert pass_at_k(5, 4, 2) == 1.0


def test_pass_at_k_rejects_impossible_inputs():
    for bad in ((3, 1, 5), (3, 4, 1), (3, -1, 1), (3, 1, 0)):
        with pytest.raises(ValueError):
            pass_at_k(*bad)


def test_pass_at_1_is_the_pass_fraction_for_any_n():
    for n, c in ((1, 1), (4, 1), (16, 5)):
        assert pass_at_k(n, c, 1) == pytest.approx(c / n)


def test_bootstrap_interval_is_deterministic_and_contains_the_mean():
    vals = [1.0] * 30 + [0.0] * 70
    lo, hi = bootstrap_ci(vals, seed=3)
    assert (lo, hi) == bootstrap_ci(vals, seed=3)
    assert lo < 0.30 < hi and 0.20 < lo and hi < 0.40


def test_interval_narrows_with_more_problems():
    small = bootstrap_ci([1.0] * 3 + [0.0] * 7)
    large = bootstrap_ci([1.0] * 30 + [0.0] * 70)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_summarize_skips_problems_with_too_few_samples_and_says_so():
    s = summarize([(4, 2), (4, 0), (1, 1)], k=2)
    assert s["problems"] == 2 and s["skipped"] == 1


def test_paired_difference_separates_a_real_gap_and_not_noise():
    gap = paired_difference([1.0] * 40 + [0.0] * 10, [0.0] * 25 + [1.0] * 25)
    assert gap["separated"] and gap["mean"] == pytest.approx(0.30, abs=0.01) or gap["separated"]
    same = paired_difference([1.0, 0.0, 1.0, 0.0] * 10, [0.0, 1.0, 1.0, 0.0] * 10)
    assert not same["separated"] and same["mean"] == 0.0
    with pytest.raises(ValueError):
        paired_difference([1.0], [1.0, 0.0])


# ---------------------------------------------------------------------------------------- dataset and parsing

PROMPT = "I would like you to implement a module named TopModule.\n - output out\nAlways drive 0.\n"
REF = "module RefModule(output out);\n  assign out = 1'b0;\nendmodule\n"
TEST = """module tb();
  wire out_ref, out_dut;
  RefModule r(.out(out_ref));
  TopModule d(.out(out_dut));
  integer errors = 0;
  initial begin
    #1; if (out_ref !== out_dut) errors = errors + 1;
    $display("Mismatches: %0d in %0d samples", errors, 1);
    $finish;
  end
endmodule
"""


def _zip(problems: int = 3) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("verilog-eval-abc/LICENSE", "MIT License")
        z.writestr("verilog-eval-abc/dataset_code-complete-iccad2023/Prob001_other_prompt.txt", "NOT ME")
        for i in range(1, problems + 1):
            pid = f"Prob{i:03d}_zero"
            z.writestr(f"verilog-eval-abc/dataset_spec-to-rtl/{pid}_prompt.txt", PROMPT)
            z.writestr(f"verilog-eval-abc/dataset_spec-to-rtl/{pid}_ref.sv", REF)
            z.writestr(f"verilog-eval-abc/dataset_spec-to-rtl/{pid}_test.sv", TEST)
    return buf.getvalue()


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("SILICONFORGE_HOME", str(tmp_path))
    calls = []

    def opener(url):
        calls.append(url)
        return _zip()

    root = ve.fetch(opener=opener)
    return root, calls


def test_fetch_keeps_only_the_spec_to_rtl_problems_and_the_license(dataset):
    root, calls = dataset
    assert len(ve.load_problems(root)) == 3 and (root / "LICENSE").read_text() == "MIT License"
    assert "NOT ME" not in "".join(p.read_text() for p in root.glob("*_prompt.txt"))
    assert calls == [f"https://github.com/{ve.REPO}/archive/{ve.PINNED_COMMIT}.zip"]  # the pinned commit, nothing else


def test_a_second_fetch_uses_the_local_copy(dataset):
    _, calls = dataset
    ve.fetch(opener=lambda u: pytest.fail("downloaded again"))
    assert len(calls) == 1


def test_selection_is_reproducible_ordered_and_a_subset(dataset):
    root, _ = dataset
    probs = ve.load_problems(root)
    assert [p.id for p in ve.select(probs, 2, seed=1)] == [p.id for p in ve.select(probs, 2, seed=1)]
    sub = ve.select(probs, 2, seed=1)
    assert len(sub) == 2 and [p.id for p in sub] == sorted(p.id for p in sub)
    assert len(ve.select(probs, None)) == 3 and len(ve.select(probs, 99)) == 3


def test_testbench_summary_parsing():
    assert ve.parse_testbench("noise\nMismatches: 0 in 100 samples\n") == (0, 100)
    assert ve.parse_testbench("Mismatches: 3 in 50 samples\nMismatches: 0 in 50 samples") == (0, 50)  # last one wins
    assert ve.parse_testbench("crashed") is None


def test_extract_module_prefers_the_block_with_the_top_module_and_drops_thinking():
    reply = ("<think>```verilog\nmodule wrong; endmodule\n```</think>\nsure\n```verilog\nmodule helper; endmodule\n```\n"
             "```verilog\nmodule TopModule(output out);\n assign out = 0;\nendmodule\n```")
    assert ve.extract_module(reply).startswith("module TopModule")
    assert ve.extract_module("module TopModule; endmodule") == "module TopModule; endmodule"


# ------------------------------------------------------------------------------------------------- grading


class FakeRunner:
    """Pretends to be iverilog/vvp from the file contents: enough to test control flow without the tools."""

    name = "fake"

    def __init__(self):
        self.commands = []

    def run(self, cmd, cwd, timeout=600, env=None, isolate=False):
        from services.orchestrator.eda.runner import RunResult

        self.commands.append(cmd)
        if cmd[:2] == ["iverilog", "-V"]:
            return RunResult(0, "Icarus Verilog version 99", "", 0.0)
        dut = (Path(cwd) / "dut.sv").read_text()
        if cmd[0] == "iverilog":
            if "syntax error" in dut:
                return RunResult(1, "", "dut.sv:2: syntax error", 0.0)
            return RunResult(0, "", "", 0.0)
        if "syntax error" in dut:
            return RunResult(90, "", "dut.sv:2: syntax error", 0.0)
        bad = "assign out = 1'b1" in dut
        return RunResult(0, f"Mismatches: {1 if bad else 0} in 1 samples\n", "", 0.0)

    def has_tool(self, tool):
        return True


GOOD = "module TopModule(output out);\n  assign out = 1'b0;\nendmodule"
WRONG = "module TopModule(output out);\n  assign out = 1'b1;\nendmodule"
BROKEN = "module TopModule(output out);\n  syntax error here\nendmodule"


def _fence(code):
    return f"```verilog\n{code}\n```"


def test_grading_distinguishes_compile_failure_wrong_answer_and_pass(dataset, tmp_path):
    root, _ = dataset
    p = ve.load_problems(root)[0]
    r = FakeRunner()
    assert ve.grade(r, tmp_path / "a", p, GOOD).passed
    wrong = ve.grade(r, tmp_path / "b", p, WRONG)
    assert not wrong.passed and wrong.compiled and wrong.mismatches == 1
    broken = ve.grade(r, tmp_path / "c", p, BROKEN)
    assert not broken.passed and not broken.compiled and "syntax error" in broken.detail


def test_grading_writes_the_hidden_testbench_only_for_the_grader(dataset, tmp_path):
    ve.compile_diagnostic(FakeRunner(), tmp_path / "d", GOOD)
    assert not (tmp_path / "d" / "test.sv").exists()  # the diagnostic path never sees the scoring test


# ----------------------------------------------------------------------------------------------------- arms


class Script:
    def __init__(self, replies):
        self.replies, self.prompts, self.temps = list(replies), [], []

    def __call__(self, system, user, temperature):
        self.prompts.append(user)
        self.temps.append(temperature)
        return LLMReply(self.replies.pop(0), prompt_tokens=100, completion_tokens=20, latency_s=0.5)


def test_single_shot_makes_one_call(dataset):
    root, _ = dataset
    llm = Script([_fence(GOOD)])
    a = build_arm("single").solve(ve.load_problems(root)[0], llm, FakeRunner())
    assert a.calls == 1 and a.rtl == GOOD and a.completion_tokens == 20


def test_feedback_repairs_a_compile_error_with_the_compiler_message_and_marks_the_line(dataset):
    root, _ = dataset
    llm = Script([_fence(BROKEN), _fence(GOOD)])
    a = build_arm("feedback", rounds=3).solve(ve.load_problems(root)[0], llm, FakeRunner())
    assert a.rtl == GOOD and a.compiled and a.rounds == 2 and a.calls == 2
    second = llm.prompts[1]
    assert "does not compile" in second and "syntax error" in second and ">>   2 |   syntax error here" in second


def test_feedback_stops_at_the_round_limit_and_reports_the_candidate_as_not_compiling(dataset):
    root, _ = dataset
    a = build_arm("feedback", rounds=2).solve(ve.load_problems(root)[0], Script([_fence(BROKEN)] * 2), FakeRunner())
    assert a.calls == 2 and a.compiled is False


def test_the_plain_feedback_arm_omits_the_line_markers_and_history(dataset):
    root, _ = dataset
    llm = Script([_fence(BROKEN), _fence(BROKEN), _fence(GOOD)])
    build_arm("feedback-plain", rounds=3).solve(ve.load_problems(root)[0], llm, FakeRunner())
    assert ">>" not in llm.prompts[1] and "Earlier attempts" not in llm.prompts[2]
    ctx = Script([_fence(BROKEN), _fence(BROKEN), _fence(GOOD)])
    build_arm("feedback", rounds=3).solve(ve.load_problems(root)[0], ctx, FakeRunner())
    assert "Earlier attempts" in ctx.prompts[2]


def test_sampled_arm_stops_at_the_first_candidate_that_compiles_and_explores_with_higher_temperature(dataset):
    root, _ = dataset
    llm = Script([_fence(BROKEN)] * 2 + [_fence(GOOD)])
    a = build_arm("sampled", rounds=2, candidates=3).solve(ve.load_problems(root)[0], llm, FakeRunner(), 0.0)
    assert a.compiled and a.rtl == GOOD and a.calls == 3
    assert llm.temps[0] == 0.0 and llm.temps[-1] == 0.7


def test_unknown_arm_is_an_error():
    with pytest.raises(ValueError, match="unknown arm"):
        build_arm("magic")


# ------------------------------------------------------------------------------------------ runner / result


def test_run_produces_a_self_describing_versioned_result(dataset, tmp_path):
    root, _ = dataset
    llm = Script([_fence(GOOD), _fence(WRONG), _fence(BROKEN)])
    res = run_verilogeval(build_arm("single"), llm, FakeRunner(), model="fake-model", root=root, seed=0)
    assert res["schema"] == SCHEMA and res["suite"] == ve.SUITE and res["runner"] == "fake"
    assert res["dataset"]["commit"] == ve.PINNED_COMMIT and res["selection"]["count"] == 3 and res["selection"]["seed"] == 0 and res["selection"]["all"] is True and res["selection"]["split"] == "all"
    agg = res["pass_at_1"]
    assert agg["mean"] == pytest.approx(1 / 3) and agg["problems"] == 3
    assert res["compile_rate"] == pytest.approx(2 / 3, abs=1e-3) and res["tools"]["iverilog"].startswith("Icarus")
    assert res["tokens"] == {"prompt": 300, "completion": 60, "calls": 3}
    assert [p["c"] for p in res["per_problem"]] == [1, 0, 0]
    path = write_result(res, tmp_path)
    assert load_result(path)["model"] == "fake-model" and path.name.startswith("eval-verilogeval-spec2rtl-fake-model-single-")


def test_a_failing_model_call_is_a_failed_sample_and_is_counted_not_hidden(dataset):
    root, _ = dataset

    def llm(system, user, temperature):
        raise RuntimeError("server went away")

    res = run_verilogeval(build_arm("single"), llm, FakeRunner(), model="m", root=root)
    assert res["errors"] == 3 and res["pass_at_1"]["mean"] == 0.0
    assert "server went away" in res["per_problem"][0]["samples"][0]["error"]


def test_multiple_samples_use_the_sampling_temperature_and_pass_at_k(dataset):
    root, _ = dataset
    llm = Script([_fence(GOOD), _fence(WRONG)] * 3)
    res = run_verilogeval(build_arm("single"), llm, FakeRunner(), model="m", n=2, k=2, sample_temperature=0.8, root=root)
    assert set(llm.temps) == {0.8} and res["pass_at_2"]["mean"] == 1.0  # one of two passes: pass@2 is certain
    assert res["pass_at_2"]["problems"] == 3


def test_load_result_rejects_other_schemas(tmp_path):
    f = tmp_path / "x.json"
    f.write_text(json.dumps({"schema": "something/else"}))
    with pytest.raises(ValueError):
        load_result(f)


# ------------------------------------------------------------------- real tools (skipped without Icarus Verilog)


@pytest.mark.real_eda
def test_the_real_grader_with_icarus(dataset, tmp_path):
    import shutil

    if shutil.which("iverilog") is None or shutil.which("vvp") is None or shutil.which("bash") is None:
        pytest.skip("iverilog/vvp/bash not on PATH")
    from services.orchestrator.eda.runner import NativeRunner

    root, _ = dataset
    p = ve.load_problems(root)[0]
    r = NativeRunner()
    assert ve.grade(r, tmp_path / "a", p, GOOD).passed
    assert ve.grade(r, tmp_path / "b", p, WRONG).mismatches == 1
    assert not ve.grade(r, tmp_path / "c", p, BROKEN).compiled
    assert ve.compile_diagnostic(r, tmp_path / "d", BROKEN) != "" and ve.compile_diagnostic(r, tmp_path / "e", GOOD) == ""


# ------------------------------------------------------------------------- testbench patch and exclusions


def test_the_wave_dump_patch_removes_only_the_dump_block():
    tb = (
        "module tb();\n  logic a;\n  initial begin \n    $dumpfile(\"wave.vcd\");\n"
        "    $dumpvars(1, stim1.clk, tb_mismatch ,a );\n  end\n  wire tb_match;\n  initial $display(\"Mismatches: 0 in 1 samples\");\nendmodule\n"
    )
    out = ve.patch_testbench(tb)
    assert "$dumpfile" not in out and "$dumpvars" not in out
    assert "wire tb_match;" in out and 'Mismatches: 0 in 1 samples' in out and "module tb();" in out
    assert ve.patch_testbench(out) == out  # idempotent
    assert ve.patch_testbench("no dump here") == "no dump here"


def test_grading_runs_the_patched_testbench(dataset, tmp_path):
    root, _ = dataset
    p = ve.load_problems(root)[0]
    p.test = p.test.replace("integer errors = 0;", 'integer errors = 0;\n  initial begin\n $dumpfile("w.vcd");\n $dumpvars(1, errors);\n end')
    ve.grade(FakeRunner(), tmp_path / "g", p, GOOD)
    assert "$dumpvars" not in (tmp_path / "g" / "test.sv").read_text()


def test_excluded_problems_are_left_out_by_default_and_recorded_in_results(dataset, tmp_path, monkeypatch):
    root, _ = dataset
    monkeypatch.setattr(ve, "EXCLUDED", {"Prob002_zero": "broken on purpose"})
    assert [p.id for p in ve.load_problems(root)] == ["Prob001_zero", "Prob003_zero"]
    assert len(ve.load_problems(root, include_excluded=True)) == 3
    res = run_verilogeval(build_arm("single"), Script([_fence(GOOD)] * 2), FakeRunner(), model="m", root=root)
    assert res["dataset"]["excluded"] == {"Prob002_zero": "broken on purpose"} and res["selection"]["count"] == 2
    assert "Icarus Verilog 14" in res["dataset"]["testbench_patch"]


# ---------------------------------------------------------------------------- trajectories, split, environment


def _problems(n=40):
    return [ve.Problem(f"Prob{i:03d}_x", "p", "r", "t") for i in range(1, n + 1)]


def test_split_is_disjoint_complete_stable_and_about_the_requested_fraction():
    ps = _problems(100)
    train, test = ve.split_problems(ps, "train"), ve.split_problems(ps, "test")
    assert {p.id for p in train}.isdisjoint({p.id for p in test}) and len(train) + len(test) == 100
    assert len(test) == 35 and ve.split_problems(ps, "all") == ps
    assert [p.id for p in test] == [p.id for p in ve.split_problems(list(reversed(ps)), "test")][::-1]  # order does not matter
    with pytest.raises(ValueError):
        ve.split_problems(ps, "dev")


def test_removing_a_problem_moves_only_that_problem_between_splits():
    ps = _problems(100)
    before = {p.id for p in ve.split_problems(ps, "test")}
    after = {p.id for p in ve.split_problems(ps[:-1], "test")}
    assert len(before ^ after) <= 3  # the rounding of 35% can shift one boundary problem, not reshuffle the split


def test_trajectories_record_full_text_and_export_only_verified_training_samples(dataset, tmp_path):
    from services.orchestrator.evals.trajectory import SCHEMA as TSCHEMA
    from services.orchestrator.evals.trajectory import TrajectoryWriter, export_sft, read

    root, _ = dataset
    w = TrajectoryWriter(tmp_path / "t" / "a.jsonl")
    llm = Script([_fence(GOOD), _fence(WRONG), _fence(GOOD)])
    res = run_verilogeval(build_arm("single"), llm, FakeRunner(), model="m", root=root, trajectory=w)
    recs = list(read([w.path]))
    assert len(recs) == 3 == w.count and res["selection"]["trajectories"] == "a.jsonl" and all(r["schema"] == TSCHEMA for r in recs)
    first = recs[0]
    assert first["calls"][0]["user"].startswith("I would like you") and "TopModule" in first["calls"][0]["reply"]
    assert first["graded"]["passed"] is True and recs[1]["graded"]["passed"] is False

    out = tmp_path / "sft.jsonl"
    info = export_sft([w.path], out, allowed_problems=None)
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert info["examples_written"] == len(lines) == 2  # the wrong answer is not exported
    assert [m["role"] for m in lines[0]["messages"]] == ["system", "user", "assistant"]
    assert lines[0]["messages"][2]["content"].startswith("```verilog\nmodule TopModule")


def test_export_respects_the_split_and_deduplicates_per_problem(tmp_path):
    from services.orchestrator.evals.trajectory import TrajectoryWriter, export_sft

    w = TrajectoryWriter(tmp_path / "b.jsonl")
    for pid, code in (("P1", "module A(); endmodule  // v1"), ("P1", "module A(); endmodule  // v1"), ("P1", "module A(); endmodule  // v2"),
                      ("P1", "module A(); endmodule  // v3"), ("P2", "module B(); endmodule  // held out")):
        w.write({"problem": pid, "model": "m", "rtl": code, "graded": {"passed": True},
                 "calls": [{"system": "s", "user": f"u-{pid}", "reply": "r"}]})
    out = tmp_path / "o.jsonl"
    info = export_sft([w.path], out, allowed_problems={"P1"}, per_problem=2)
    assert info["examples_written"] == 2 and info["problems"] == 1  # P2 is outside the training split; v1 counted once; v3 over the cap


def test_runs_on_the_test_split_only_touch_test_problems(dataset):
    root, _ = dataset
    res = run_verilogeval(build_arm("single"), Script([_fence(GOOD)] * 3), FakeRunner(), model="m", root=root, split="test")
    expected = {p.id for p in ve.split_problems(ve.load_problems(root), "test")}
    assert {p["id"] for p in res["per_problem"]} == expected and res["selection"]["split"] == "test"


def test_environment_rewards_follow_the_verifier_cascade_and_hide_the_testbench(dataset):
    from services.orchestrator.evals.env import COMPILE_PENALTY, PASSED, VerilogEnv
    from services.orchestrator.evals.env import WRONG as NO_REWARD

    root, _ = dataset
    env = VerilogEnv(FakeRunner(), split="all", max_turns=2, root=root)
    obs, info = env.reset(seed=1)
    assert obs["turn"] == 0 and obs["diagnostic"] == "" and obs["prompt"].startswith("I would like you")
    obs, r, done, trunc, info = env.step(_fence(BROKEN))
    assert r == COMPILE_PENALTY and not done and "syntax error" in obs["diagnostic"] and obs["turn"] == 1
    obs, r, done, _, info = env.step(_fence(GOOD))
    assert r == PASSED and done and info["passed"] is True and "Mismatches" not in json.dumps(obs)
    env.reset(seed=1)
    assert env.step(_fence(WRONG))[1:3] == (NO_REWARD, True)  # compiles, fails the hidden test: episode over
    env.reset(seed=1)
    env.step(_fence(BROKEN))
    assert env.step(_fence(BROKEN))[1:3] == (COMPILE_PENALTY, True)  # turn budget exhausted
    with pytest.raises(RuntimeError, match="reset"):
        VerilogEnv(FakeRunner(), root=root).step("x")
