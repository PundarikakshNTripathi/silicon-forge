# Searching place-and-route knobs

The original optimizer (`rl/optimizer.py`) is a stateless bandit over eight hand-written recipes; it has no model of how the knobs
affect the result and had never been compared with anything. This page describes what was added and what the first comparison
showed. The short version: the new optimizer did **not** beat the simple baselines in this experiment.

## What exists

- `rl/bo.py`: a search space of float, integer and categorical knobs (`FP_CORE_UTIL`, `PL_TARGET_DENSITY_PCT`, `GRT_ADJUSTMENT`,
  `SYNTH_STRATEGY` by default), uniform random and Latin-hypercube samplers, and `GPBayesOptimizer` (Matern 5/2 Gaussian process,
  lengthscale from marginal likelihood, expected improvement, numpy only). Failed flows are imputed just below the worst valid
  result so they steer the model away without setting its scale.
- `rl/ppa_env.py`: `evaluate_knobs` for arbitrary knob dictionaries, an optional quality-only reward (no runtime term, which depends
  on machine load), and **flow failures as outcomes**: a LibreLane run that does not finish (for example detailed placement failing
  at too high a utilization) is recorded, scored -50, cached and logged instead of aborting the search.
- `benchmarks/bench_ppa.py`: runs default, random, Latin hypercube, the bandit and the Gaussian-process optimizer at the same
  evaluation budget on the real flow.

## The experiment

`up_down_counter` on sky130A through WSL (LibreLane 3.0.17). A probe run with default knobs sets the objective: target clock 9.5 ns
(10% over the measured critical path of 8.67 ns), area and power budgets equal to the default run's (1,155 µm², 0.113 mW). Reward
is the signoff-aware quality score; higher is better. 8 evaluations per run, 3 seeds for the stochastic strategies (the bandit tries
its recipes in a fixed order, so it has one run). Result: `benchmarks/results/ppa-up_down_counter-full-2026-11-21.json`.

| Strategy | Runs | Best reward, mean | Range over seeds | Failed flows per run |
| --- | --- | --- | --- | --- |
| default knobs | 1 | 4.209 | n/a | 0 |
| random | 3 | 4.534 | 4.318 to 4.644 | 1.0 |
| Latin hypercube | 3 | 4.518 | 4.323 to 4.623 | 2.0 |
| bandit (original) | 1 | 4.653 | n/a | 1 |
| Gaussian process | 3 | 4.382 | 4.234 to 4.649 | 0.33 |

## What this shows and what it does not

- Every search strategy improved on the default knobs, by about 0.2 to 0.4 reward points (a few percent of area and power).
- The strategies are **not separated**: the ranges overlap completely, with three seeds and eight evaluations. The Gaussian-process
  mean is the lowest of the stochastic strategies: two of its three runs ended at 4.234 and 4.262, below the lowest random run
  (4.318), and the third reached 4.649, level with the best result of the whole experiment. With three seeds that spread is
  noise-sized, not a finding.
- On this design the flow fails when core utilization is 59% or more (3 of the 21 random points, all at 59, 63 and 64%). The failure
  handling is therefore needed, and the knob range should be narrowed for this design.
- One small design, one process, eight evaluations. A Gaussian process normally needs more points than this to learn a
  four-dimensional landscape, and the objective has a flat region near its optimum; a larger budget is the experiment that could
  change this conclusion, and it has not been run.

The pipeline keeps the original bandit as its default optimizer. `GPBayesOptimizer` is available and tested (on a synthetic
objective it beats random search, which checks the implementation, not place and route), but there is no evidence here that it
should replace the bandit.
