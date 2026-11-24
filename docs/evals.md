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

The NotSoTiny runs (`scripts/notsotiny.py`) write `eval-notsotiny-26-07-<arm>-<date>.json` with the dataset revision, the frozen sample name and seed, the grader, and one record per task (verdict, whether Yosys read the project, calls, tokens, seconds). Verdicts other than `equivalent` are `not_proven`, `syntax_error`, `error` (the grader failed), `provider_error` (the model call failed) and `void_reference_lookup` (an agent run whose transcript shows it looked for the answer); all count as misses, and the counts are in the file.

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

## Pairwise equivalence grading

`services/orchestrator/evals/equiv.py` grades a candidate module against a reference by proof instead of by testbench: Yosys `equiv_make`,
`equiv_simple`, then `equiv_induct -seq 30` (the recipe of [NotSoTiny](https://arxiv.org/abs/2512.20823)). The verdict is `equivalent`,
`not_proven` or `error`; it never says "different", because an unproven pair may be a different design or one that bounded induction could
not close. `self_check` runs reference-versus-reference and reference-versus-mutant so a grader is checked before it scores anything.
It needs no model. The NotSoTiny comparison below uses it.

## A first hidden-test comparison: NotSoTiny (40 tasks)

[NotSoTiny](https://arxiv.org/abs/2512.20823) (HPAI-BSC, Apache-2.0, pinned to revision `c561f340`) gives a Tiny Tapeout project with one module's body masked
out; the answer is graded by Yosys formal equivalence against the real module, which no arm can see. 40 tasks were drawn with a fixed seed from the ones whose
reference passes the grader against itself, and the list was committed (`benchmarks/notsotiny/sample-seed0-n40.json`) before any arm ran. The dataset is downloaded
to the data directory (`python scripts/notsotiny.py fetch`), never committed.

Arms: `single` (one reply), `feedback` (up to two repairs from Yosys' parse and elaboration message only, never the reference), and a **plain coding agent**
(`claude -p` or `agy`, given the project, the task text and a command that runs the same Yosys check, nothing from this repository). The agents ran on the
first 20 tasks of the sample, so they are compared with the other arms on those 20.

| Setup | Tasks | Equivalent (95% interval) | Paired difference from the first row |
| :--- | ---: | :--- | :--- |
| Qwen3.5-9B Q4_K_M, local, single | 40 | 12.5% (2.5 to 25.0), 5 of 40 | |
| Qwen3.5-9B Q4_K_M, local, feedback | 40 | 12.5% (2.5 to 25.0), 5 of 40 | 0.0 points |
| Gemini 3.8 Flash (agy), single | 40 | 30.0% (15.0 to 45.0), 12 of 40 | +17.5 points (+7.5 to +30.0) |
| Gemini 3.8 Flash (agy), feedback | 40 | 32.5% (17.5 to 47.5), 13 of 40 | +20.0 points (+7.5 to +32.5) |
| Plain coding agent, Gemini (agy), 20 tasks | 20 | 20.0% (5.0 to 40.0), 4 of 20 | +15.0 points (0.0 to +30.0) |
| Plain coding agent, Claude (`claude -p`), 20 tasks | 20 | 30.0% (10.0 to 50.0), 6 of 20 | +25.0 points (+10.0 to +45.0) |

(Regenerate with `python scripts/notsotiny.py table FILE...`; the first row of the command is the base. Result files are in `benchmarks/results/eval-notsotiny-26-07-*.json`.)

What it says, in the order of the stop rule written down beforehand:

- **Feedback from a parse check did not help.** Local: identical to single (5 of 40 both). Gemini: one more task (13 against 12), which is what a second draw of the same
  model can do, because the repair step never ran: every completed Gemini reply parsed on the first try (one task timed out in the provider both times and counts as a miss). On the local model the repair ran on all 16 tasks whose first
  reply did not parse and fixed none of them. A check that only says "it parses" cannot tell the model whether the module does the right thing.
- **The plain coding agents did not do better than the same model in one reply, and Claude's 30% on 20 tasks is level with Gemini's single-reply 30% on 40.** The paired difference
  of the Claude agent over Gemini single on the shared 20 is +10 points with an interval from 0.0 to +25.0 (not separated); the Gemini agent is level with Gemini single (0.0). The agent ran
  Yosys on its own file and still had no way to learn whether the logic was right; 6 of the 20 Gemini agent runs hit its 10-minute limit and one Claude run hit 700 s.
- **What is separated is model size.** Gemini over the local 9B model: +17.5 points (+7.5 to +30.0) on the same 40 tasks.
- So on this task, **a Yosys parse verdict adds nothing measurable, and a plain coding agent with Yosys did not beat one reply**. **Not tested here: SiliconForge's own agent loop was not run as an arm**, so this run says nothing yet about
  our loop against a plain agent on the same model. The only tool-gated arm is the parse-error repair loop. (A first try of our loop through the `agy` bridge was stopped: four model calls took over 15 minutes on one task, so 20 tasks is impractical that way. A run on the local 9B, to compare with its single and feedback rows, needs a GPU window and has not been done.) This is the outcome the stop rule names for that arm, and it applies: nothing here supports training spend, and the pitch is the verified flow around a design (lint, formal, equivalence, place and route), not a pass-rate gain on module completion.

Limits of this run, all real:

- 40 tasks (20 for the agents), one run each, one dataset revision. The intervals are wide; "not separated" means unknown, not "equal". The graded gap between Claude and the others could be real.
- Failure shapes differ by arm. The local model fails to parse on 16 of 40 tasks; in the one I opened the reply held several modules, so the whole-module splice nested them (I did not inspect all 16). The prompt asks for the body only, so this is
  counted as a miss, but a more forgiving splice (keep only the module with the target name) would score the local arms higher. It was not changed after seeing results.
  One task (`ttgf0p2_0259`) ends in a grader error ("gate module contains memories or processes") for every arm, so it is a miss for all of them and may be a task this grader cannot judge; it stays in the sample because the sample was frozen first.
- The tasks are public and recent, so some contamination of the cloud models is possible; the local model's training cut-off is earlier.
- **An earlier run of the plain agent was thrown away.** The first Gemini agent run scored 20 of 20, and the transcript showed why: the agent found the dataset file on this machine and read the reference answer.
  The scores were deleted, the prompt now forbids looking outside the folder, the dataset is scrambled on disk while an agent runs (`notsotiny.scrambled_on_disk`), every run's full transcript is saved and
  scanned for lookups (`plain_agent.audit`; a run with one is void and scored as a miss), and both agents were rerun. Neither rerun had a flagged run. This guards against reading the file on this machine and
  against the public dataset URL appearing in a transcript; it does not prove an agent never recalled an answer from training.
- The cloud agents ran on the author's subscriptions, which have usage limits (the Claude arm hit one on a first attempt, and that attempt was discarded and rerun).

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
- **Thinking on with a 16,000-token budget: the budget is still the limit.** The same 40 problems with `--max-tokens 16000` and a
  20k context (`evals run ... --thinking on --max-tokens 16000 --ctx 20480`, `eval-...-single-thinking-on-16k-*.json`): **30.0% pass@1
  (95% interval 17.5 to 45.0)**, against 25.0% at a 6,000-token cap and 47.7% over all 155 problems with thinking off (a different, larger
  set). **23 of the 40 answers still ran to the 16,000-token limit while reasoning** (6,000: 25 of 40), so they contain no module. Of the 17
  that finished, 12 passed. The run produced 430,598 completion tokens in 179 minutes, about 14 times the non-thinking run's tokens per problem (10,765 against 751).
  What this says: on this model and these problems, thinking spends far more than 16k tokens on the hard problems, and the finished answers
  are good (12 of 17) but few; a larger budget or a more efficient reasoner would be needed to know what thinking is worth, and the 8 GB
  card does not fit a much larger context. Caveat: WSL, which grades the answers, was unavailable for part of this run (the disk had
  filled), so some finished answers may have been graded as failures that would have passed; the 23 budget hits do not depend on grading.
  It does not show that thinking helps or hurts.
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
