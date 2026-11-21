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
| `feedback-guided` | As `feedback`, plus the knowledge-pack sections that match the compiler's error lines (`docs/knowledge-packs.md`). Comparing it with `feedback` measures whether the guidance helps beyond the raw message. |
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

## Fine-tuning on verified samples (one round, measured)

One round of rejection-sampling fine-tuning, using the pieces above:

```bash
# 1. sample the base model on the training problems and keep the graded samples (needs torch, transformers, peft)
siliconforge evals run verilogeval --hf Qwen/Qwen2.5-Coder-1.5B-Instruct --split train --n 8 --save-trajectories
# 2. keep only verified answers, at most two per problem, training problems only
siliconforge evals export-sft trajectories/traj-*.jsonl --out sft.jsonl --split train --per-problem 2
# 3. LoRA fine-tune (plain PyTorch loop, loss on the answer tokens only)
python scripts/train_lora.py --model Qwen/Qwen2.5-Coder-1.5B-Instruct --data sft.jsonl --out adapters/rft1
# 4. judge base and tuned model on the held-out test problems, same grader, n=8 samples each
siliconforge evals run verilogeval --hf Qwen/Qwen2.5-Coder-1.5B-Instruct --split test --n 8 --tag base
siliconforge evals run verilogeval --hf Qwen/Qwen2.5-Coder-1.5B-Instruct --adapter adapters/rft1 --split test --n 8 --tag rft1
siliconforge evals compare <base>.json <rft1>.json
```

### Result of the first run (Qwen2.5-Coder-1.5B-Instruct, one round, one seed)

| Step | Outcome |
| :--- | :--- |
| Sampling the base model on the 101 training problems, 8 samples each | pass@1 6.3% (95% interval 3.7 to 9.4), compile rate 42.7%; 38 minutes |
| Verified answers kept (at most two per problem) | **37 examples from 23 problems** |
| LoRA (rank 16, alpha 32, learning rate 1e-4, 3 epochs, answer-only loss) | mean loss 0.162, 0.130, 0.111; 35 seconds; 71 MB adapter |
| Base model on the 54 held-out test problems, 8 samples each | pass@1 **14.8%** (8.8 to 22.0), compile rate 46.3% |
| Tuned model on the same problems | pass@1 **22.9%** (14.1 to 32.2), compile rate 59.0% |
| Paired comparison (`evals compare`) | **+8.1 points, 95% interval +3.7 to +12.7**: the intervals separate them |

Per problem, the tuned model did better on 18 of 54, worse on 6 and the same on 30; it solved at least once 22 problems against 19.
Files: `eval-...hf-Qwen-Qwen2.5-Coder-1.5B-Instruct-*-rft-train-samples-*.json`, `...-single-test-base-*.json`,
`...-lora-adapter-single-test-rft1-*.json`.

How far to trust it:

- **Train and test problems are disjoint** (split by a hash of the problem id; zero shared ids), and only the training split was
  sampled for training data. VerilogEval contains families of near-identical problems, so disjoint ids do not guarantee disjoint
  content. A name-stem check found 3 of the 54 test problems resembling a training problem; their mean gain was 0.083 against 0.081
  for the other 51, so the result does not hinge on them. This is a heuristic, not a proof.
- **One round, one training seed, one small model.** The base model is weak (6% on the training split), so the 37 examples are
  mostly easy problems; the gain is larger in compile rate (+12.7 points) than in anything else, which matches the expectation that
  most of what was learned is producing complete, compilable modules. The result is a demonstration that the pipeline works end to
  end, not a claim about 7-9B models and not a rival to published reinforcement-learning results. A second round, more problems and
  several training seeds are the obvious next steps and have not been run.
- The measurement is on VerilogEval's own testbenches, as everywhere else in this document, and the grader is the one validated above.

Adapters are not committed; they derive from a model with its own license. The recipe is the four commands above.

## Limits

- One suite so far. CVDP (Apache-2.0) is the next candidate; ChipBench declares no license and is not used.
- Local models run **with thinking off**: the local provider in `configs/models.yaml` sends `enable_thinking: false` with every
  request, and that per-request switch overrides the server's `--reasoning` flag. (A first version of these evals recorded the
  server flag as if it were the setting, and a run labelled "thinking on" was in fact another non-thinking run; it was found because
  its outputs were identical to the non-thinking run's, and was replaced.) Use `--thinking on` to switch thinking on for a
  comparison. Latency numbers are for one request at a time on one 8 GB GPU.
- **Thinking on, measured once, inconclusive.** Qwen3.5-9B with `--thinking on` on the 40-problem seed-0 subset scored 25.0%
  pass@1 (95% interval 12.5 to 40.0) with a 27.5% compile rate, against 47.7% over all 155 problems with thinking off
  (`eval-...-single-subset40-thinking-on-*.json`). The cause is the token budget, not the model: the catalog caps completions at
  6,000 tokens, and 25 of the 40 thinking-on answers ran to that limit while still reasoning, so none of them contained a finished
  module (0 of 25 compiled). Of the 15 that finished, 11 compiled and 10 passed. Mean completion length was 4,970 tokens against
  751 with thinking off, and the run took 63 minutes for 40 problems. This does not show that thinking helps or hurts; it shows
  that 6,000 tokens is too few for this model to think on these problems. Not yet measured: a larger budget (16k or more) on the
  same subset, which would take roughly two to three hours on this GPU.
- The compile diagnostic is the only feedback, so `feedback` cannot fix a design that compiles but computes the wrong
  thing. Measuring a loop that can use simulation feedback fairly needs a held-out testbench, which is what the
  SiliconForge-Bench suite (planned) is for.
