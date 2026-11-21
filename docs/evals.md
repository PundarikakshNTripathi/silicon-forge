# Evaluations

`siliconforge evals` measures a model, with a given way of using it, on a public benchmark. The aim is a number you can
repeat, with an interval around it, and a way to tell whether one setup is better than another or just luckier.

```bash
siliconforge evals fetch verilogeval                       # download the dataset (pinned commit, MIT)
siliconforge evals selfcheck verilogeval                   # grade each problem's own reference: all must pass
siliconforge evals run verilogeval --profile local --serve models/gguf/Qwen3.5-9B-Q4_K_M.gguf \
    --arm single --problems 60 --seed 0                    # a seeded random subset
siliconforge evals run verilogeval --profile gemini-agy --arm feedback   # Gemini through the Antigravity CLI
siliconforge evals list
siliconforge evals compare a.json b.json                   # paired difference with an interval
```

## What is measured

**Suite.** VerilogEval v2, spec-to-RTL ([NVlabs/verilog-eval](https://github.com/NVlabs/verilog-eval), MIT license, pinned to
commit `c498220`): 156 problems, each a natural-language prompt, a reference module and a self-checking testbench. It is a
public benchmark that is close to saturated for frontier models and is in the training data of many models, so read it as a
yardstick for small local models and for the effect of the harness, not as a statement about hard designs.

**Grading.** A candidate passes when it compiles together with the reference and the benchmark's testbench reports zero
mismatches, using Icarus Verilog. The tools run on the backend you choose (`--backend wsl|native|docker`), and the result
file records which, with the tool version.

**Two adjustments, both recorded in every result file:**

1. *One problem is excluded.* `Prob099_m2014_q6c` cannot pass: its prompt and testbench use outputs `Y2` and `Y4`, the
   reference module declares `Y1` and `Y3`. The denominator is 155. `selfcheck` keeps checking that the exclusion still
   holds, and fails if the problem starts to pass.
2. *The testbench's waveform-dump block is removed.* All 156 testbenches reference `tb_mismatch` in `$dumpvars` before it is
   declared, which Icarus Verilog 14 rejects, so none would compile as shipped. The block only writes a VCD.

**Does the grader work?** Two checks, both run against real Icarus Verilog through WSL:

- *Sensitivity.* Every one of the 155 reference solutions, renamed to `TopModule`, passes its own testbench (`selfcheck`).
- *Specificity.* The first `assign` of each reference was inverted: 89 of 89 mutations that touched live code were rejected.
  (Two further mutants were accepted because the crude mutator hit commented-out code, not because the testbench missed
  anything.) This check is not part of the CLI; it is a one-off done when the grader was written.

## Arms

An arm is a way of turning a problem into RTL. Every arm starts from the same statement and may use **only the compile
diagnostic of its own candidate**. The scoring testbench is never visible to an arm: if it were, the number would measure
how well an agent overfits to the grader. This is the two-level protocol of [arXiv:2606.28279](https://arxiv.org/abs/2606.28279),
and it is why the loop below is limited to what a compiler can tell you.

| Arm | What it does |
| --- | --- |
| `single` | One call. The baseline. |
| `feedback-plain` | Generate, compile; on an error ask again with the compiler's message (at most `--rounds` calls). |
| `feedback` | As above, plus the source lines the error points at and a short history of what earlier rounds tried (`docs/debug-context.md`). |
| `sampled` | Up to `--candidates` runs of `feedback`, stopping at the first candidate that compiles. |

Comparing `single`, `feedback-plain` and `feedback` on the same model and problems separates what the loop buys from what the
extra context buys.

## Statistics

For each problem the harness draws `n` samples and counts `c` passes. The score is the unbiased pass@k estimator
(Chen et al., 2021), averaged over problems; with `n = 1` it is plain accuracy at the sampling temperature you set. The 95%
interval is a percentile bootstrap over problems (4000 resamples, fixed seed), because problems are what vary between
runs of a benchmark.

`compare` takes two result files, pairs them on the problems they share, and bootstraps the difference. If the interval
contains zero, the data do not separate the two setups. With 60 problems, a single sample each and accuracy near 50%, the
interval is roughly ±12 points, so differences smaller than that are not detectable; use all problems, more samples, or
both when it matters.

## Result files

`eval-<suite>-<model>-<arm>[-<tag>]-<date>.json`, schema `siliconforge.eval/1`: suite and dataset commit, model, the arm and
its settings, `n`, `k`, temperature, the problem selection and its seed, the runner and tool version, the aggregate with its
interval, compile rate, token and call counts, wall time, and one record per problem and sample (passed, compiled,
mismatches, rounds, tokens, latency, error). A model call that fails is a failed sample with its error recorded, and the
run reports a non-zero exit code; failures are not dropped from the denominator.

Datasets are downloaded into the data directory (`/datasets` is gitignored) and never committed or redistributed.

## Trajectories, a fixed split and an environment

For fine-tuning you need the replies, not only the scores.

```bash
siliconforge evals run verilogeval --serve model.gguf --arm single --n 8 --k 1 --split train --save-trajectories
siliconforge evals export-sft ~/.../trajectories/traj-*.jsonl --out sft.jsonl --split train --per-problem 2
siliconforge evals run verilogeval --serve model.gguf --arm single --split test     # judge on problems it never saw
```

- `--split train|test|all` is a fixed partition of the problems: 35% go to `test`, chosen by hashing the problem id, so the split
  does not depend on file order and removing a problem moves only that problem. The two partitions are disjoint.
- `--save-trajectories` writes one JSON line per graded sample with the **full prompt and reply text**. That is why it is opt-in
  and why the files live under `trajectories/` (gitignored). The AI-usage ledger, by contrast, records only hashes.
- `export-sft` writes chat-format examples (`system`, `user`, `assistant`) from samples the grader **passed**, capped per problem, and
  only for problems in the split you name. Export `train`; exporting `test` would contaminate the evaluation.
- `VerilogEnv` (`services/orchestrator/evals/env.py`) is a Gymnasium-style environment (`reset`, `step`) over the same problems:
  -0.1 for a candidate that does not compile (the compiler message comes back as the observation), 0.0 for one that compiles but fails
  the testbench, 1.0 for a pass. The testbench output is never in the observation.

## Limits

- One suite so far. CVDP (Apache-2.0) is the next candidate; ChipBench declares no license and is not used.
- Local models are run with the chat template's defaults, which for the Qwen3 family includes thinking. Token counts include
  it; latency numbers are for one request at a time on one 8 GB GPU.
- The compile diagnostic is the only feedback, so `feedback` cannot fix a design that compiles but computes the wrong
  thing. Measuring a loop that can use simulation feedback fairly needs a held-out testbench, which is what the
  SiliconForge-Bench suite (planned) is for.
