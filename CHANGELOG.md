# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/), versioning: [SemVer](https://semver.org/).

## [Unreleased]
### Changed (README, docs, assets)
- README restructured around what a reader does first: a hero run screenshot, then a visual tour (web cockpit light and dark, verification, performance, layout, agent loops, the terminal UI as a recording), the agent-against-frameworks results with the benchmark's design diagram, the plots and the limits. New diagrams for the agent loop and the benchmark design; the flow diagram uses the stage hues. Screenshots are regenerated from a real WSL run by `scripts/capture_ui.py`, with the tab and theme chosen by URL (`?tab=`, `?theme=`).
- `tests/test_readme_assets.py`: every README image exists, has alt text and a size budget; every result file it cites exists; relative links resolve.
### Measured (speculative decoding and correctness)
- `evals run --spec-type` (recorded in the result file). Qwen3.5-9B on all 155 VerilogEval problems with `ngram-mod` speculation: 49.0% pass@1 against 47.7% without it, paired difference +1.3 points (95% interval -1.3 to +3.9), not separated; 4 of 155 problems changed outcome (3 up, 1 down), so output is not identical but there is no detectable harm; 3% less wall time on fresh generation. One run per arm (`docs/providers.md`).
### Added (agent)
- **Project memory** (`memory: true`): a `remember` tool and `.siliconforge/memory.md` that every later session reads, plain Markdown you can edit, secret-masked and bounded; `siliconforge memory show|add|clear` and `/memory` in the TUI (`docs/workspaces.md`).
- **`investigate` subagent**: a read-only helper with its own fresh context that returns only a short answer, so searching a large project does not fill a small model's window. It cannot edit or recurse.
- **GitHub Action** `.github/actions/flow`: `siliconforge flow check` then `flow run --locked`, report uploaded even on failure; inputs go through the environment, and a test enforces that (`docs/flow.md`).
### Added (web UI)
- **Evals page, "Agent loops" tab**: the AgentBench-SF head-to-head from the committed result files (task success with intervals and the content-correct reading, a time, tools, recovery and cost table, a per-task matrix, package versions). `/api/benchmarks` drops the per-run records of AgentBench files (hundreds of KB) and serves their summaries.
- **Insights page** (was Cost): the workspace agent's time split (model, tools, everything else), tool success with failures by class, recovery and tokens per turn, from the recorded spans, with an honest empty state.
- Vitest for the run-view derivation and a check that every stage has a distinct hue in the generated token file; `pnpm run test` runs in CI. `SF_API` points the dev server's proxy at another API port. Agent conversation tool steps are styled as quiet rows.
### Added (terminal UI)
- **`siliconforge tui` is now a chat with the workspace agent** (`docs/tui.md`): tool calls as live cards with spinner and timing, edits shown as diffs and approved in a modal (`y` once, `a` always for this tool, `n` decline), slash commands with inline completion, `Shift+Tab` to cycle permission modes, `Esc` to interrupt a turn, an animated splash (the logo's conductor drawing itself through five layers), a status line with a real or simulated stamp, tokens, cost and context stage. The pipeline run viewer moves to `siliconforge tui --run` and gets the layer-hued stage strip (solid for real tools, hatched for a surrogate, a diamond where a stage sent the design back).
- `AgentSession.interrupt` (a `threading.Event`): a UI can stop a turn cleanly before the next model call; the turn ends as `interrupted`.
- Pilot-driven tests (`tests/test_tui_chat.py`) with a scripted model, `scripts/tui_demo.py` and `scripts/frames_to_gif.py` to regenerate the README recording. The recording uses a scripted model and says so.
### Added (agent benchmark)
- **`siliconforge agentbench`** (`docs/agent-benchmark.md`): our own agent loop against agents built with LangGraph (`create_agent`) and Pydantic AI, on 13 sandboxed tasks (comprehension, editing checked by lint or by a hidden simulation testbench, recovery under injected faults, a must-ask-first request, and prompt-injection and secret-leak probes), with the same model, tools, sandbox and step budget. Model calls are timed at one measuring proxy and tool calls at one tool seam, and the four telemetry figures are computed identically for every arm. Verdicts come from deterministic checks the agent never sees; a safety violation fails a task regardless of the answer.
- The harness is tested end to end (scripted arms prove two arms with the same script measure the same; the real frameworks run against a scripted fake model; the three simulation verifiers accept the right fix and reject the bug through the real toolchain). Extra `bench` (frameworks, benchmarks only) and a CI job for it.
- **First live results** (Qwen3.5-9B, 13 tasks, 5 trials, three independent runs): LangGraph and Pydantic AI about 92% strict task success against 71% for our loop; the paired interval touches zero. The gap is mostly two fixable things: our loop ignores a requested answer format inside its JSON envelope (content-correct 86%), and 11 of its 34 `edit_file` calls miss their target. Ours uses 23 to 30% fewer tokens and, with its guards, had 0 safety violations in 20 runs against 2 for each plain framework (0 again once the guards are added to them). Nobody asks the clarifying question on the ambiguous task.
- `agentbench compare A.json B.json [--max-drop] [--gate]`: drift detection for the same arm across two runs (paired bootstrap on per-task success, metric deltas, changed package versions); a regression needs both a large enough drop and an interval that excludes zero. Numeric tasks read an `ANSWER:` line so verbosity is not punished, a secondary content-correct score and short reply excerpts make every verdict auditable, and results carry a per-tool breakdown.
### Added (telemetry)
- **Tracing and agent metrics** (`services/orchestrator/telemetry/`, `docs/telemetry.md`): spans for agent runs (`run > step > llm | tool`, metadata only), and the four figures an agent should be judged on, with their definitions in code and tests: end-to-end latency split into reasoning / action / overhead, tool success rate with a failure taxonomy (refusals by policy counted separately), cost per task and per *solved* task, and failure-recovery rate with steps and tokens to recover.
- `chat --trace` / `agent --trace`, `siliconforge trace summary`; the API server records agent sessions into SQLite and serves `GET /api/insights/agent` and a Prometheus `GET /api/metrics`.
- Optional OpenTelemetry export (`pip install "siliconforge[otel]"`) in the GenAI semantic conventions, with original timestamps. A test caught parents being exported after children when spans shared a start time; fixed.

## [0.4.0] - 2026-11-21
First release under **AGPL-3.0-or-later** (a commercial license is available; see `COMMERCIAL.md`). The headline changes: an
evaluation harness with confidence intervals, a project flow for your own RTL, a debug-context loop for repair, an agent with
permission rules, hooks, an MCP client and context management, and a first measured fine-tuning result. The measured results,
including the null ones, are in the README. The Windows installer attached to the release is **unsigned**.

### Added (inference resilience)
- `Retry-After` (seconds or HTTP date) is parsed from OpenAI-compatible and Anthropic rate-limit responses and honoured by the retry helper instead of a random delay; a request to wait over 60 s is not waited out, so the router can fall back.
- Per-provider `rate_limit: {rpm, tpm, max_wait_s}` in the catalog: a sliding-window pacer in the router. Too long a wait falls through without tripping the circuit breaker; wait time is reported as `rate_wait_s`. Nothing is limited by default. `docs/providers.md`.
### Added (agent: MCP client)
- Manifest `mcp_servers:` lets the agent use tools from external stdio MCP servers (`mcp__<server>__<tool>`). They share the built-in path: permission modes (default `command`), allow/deny rules, hooks, loop guard, redaction, output fencing. Start-up failures are reported, not hidden; calls time out; servers stop with the session. Tested against a real stdio server (21 tests); not yet against third-party servers. `docs/workspaces.md`.
### Added (agent hooks)
- Manifest `hooks:` (`pre_tool` blocks on a non-zero exit, a failed start or a timeout; `post_tool` appends its output to the tool result). Argument lists, no shell, strict validation, environment describing the call. `docs/workspaces.md`.
### Added (local inference)
- Speculative decoding options for the managed llama-server (`--spec-type`, `--draft-model`, `--draft-max`), validated, with the draft model counted in the VRAM guard; and `benchmarks/bench_speed.py`, a speed sweep over KV type, context length and speculation. **Measured on Qwen3.5-9B / RTX 5060 Laptop:** n-gram speculation is 2.9x faster when editing a file (76% of drafted tokens accepted) and neutral for fresh generation, but greedy output differed from the plain run on 3 of 4 edit prompts (a re-run of the baseline matched itself 8 of 8), and the effect on RTL correctness is not measured, so it stays off by default. KV q4_0 saves 128 MiB, is no faster and changed every output. An earlier run that reused one prompt reported a 375 tok/s artifact and was discarded; the benchmark now sends distinct prompts, warms up, and re-runs the baseline as a control. `docs/providers.md`.
### Added (agent safety and context)
- Workspace `permissions: allow/deny` rules (`tool` or `tool(glob)`; deny wins and applies in `auto`; paths normalised so `..` and backslashes cannot dodge a pattern).
- Staged context compaction: the transcript stays inside `context_chars`; earlier turns become a deterministic digest, no model call.
- Loop guard (note at 3 identical calls, stop at 5), `<tool_output>` fencing of tool results, and redaction of keys and tokens in tool output and saved sessions.
### Added (verification)
- `siliconforge triage sim.json ...`: buckets failing runs by a normalised signature (lockstep divergences by the fields that disagree), with seeds, tests, an example and a replay line per bucket. `docs/verification.md`.
### Fixed (verification)
- `sf_cocotb_run.py` recorded an empty message for every failed test: cocotb 2.x writes the text in `error_msg`, not `message`. `sim.json` now carries the message and `error_type`. Found by running triage on a real failing simulation.
### Added (flow)
- `flow lock`, `flow lock --check` and `flow run --locked`: pin the installed Verilator, Yosys and LibreLane versions and the PDK name in `flow.lock`, and refuse to run when they differ. `docs/flow.md`.
### Added (agent)
- Opt-in saved conversations: `chat --save`, `chat --resume [ID]`, `siliconforge sessions`. Stored as JSON lines under `.siliconforge/sessions/`; ids are validated, a torn last line is skipped. Not saved unless asked, because the file holds prompts and tool output in clear text. `docs/workspaces.md`.
### Fixed (evals)
- **Thinking was not what the results said it was.** The local provider's catalog entry sends `enable_thinking: false` with every request, which overrides the server's `--reasoning` flag; result files recorded the flag. All local runs were therefore non-thinking (correct in effect), but the run labelled "thinking on" was another non-thinking run: it produced token-for-token identical output on all 40 shared problems. Added `evals run --thinking on|off|catalog` (a per-request switch, recorded as `thinking`), corrected the docs and the Evals page, and re-ran the thinking comparison with the switch actually on.
### Added (fine-tuning)
- `evals run --hf MODEL [--adapter DIR]` evaluates a Hugging Face model (with a LoRA adapter) through the same harness, grader and result schema, with batched sampling; `scripts/train_lora.py` is a plain PyTorch LoRA loop with answer-only loss; `evals/sft.py` holds the tested data preparation. Uses `dtype=` (newer transformers deprecate `torch_dtype`).
- **First measured result:** one rejection-sampling round on Qwen2.5-Coder-1.5B-Instruct (37 verified examples from 23 training problems, 35 s of LoRA training) raised pass@1 on 54 held-out problems from 14.8% to 22.9%, paired difference +8.1 points (95% interval +3.7 to +12.7); compile rate 46.3% to 59.0%. One round, one seed, a small model; see `docs/evals.md` for what it does and does not show.
### Added (place-and-route search)
- `rl/bo.py`: a Gaussian-process Bayesian optimizer over knob spaces, with random and Latin-hypercube samplers; `benchmarks/bench_ppa.py` compares default knobs, random, Latin hypercube, the original bandit and the GP optimizer on the real flow. **First result: no strategy was separated from the others (8 evaluations, 3 seeds, one design), and the GP did not beat random search.** The bandit stays the default. `docs/ppa-search.md`.
- A flow that fails to complete (detailed placement at too high a utilization, for example) is now an outcome in the PPA environment: recorded, scored -50, cached, and logged, instead of aborting the whole search.
### Changed (knowledge packs)
- A ninth pack, `iverilog_errors`, written after the first full VerilogEval run: 54 of the Qwen3-8B answers that failed to compile were the same mistake (assigning in an `always` block to an output declared as a plain net), and the packs had no entry for Icarus messages. New evaluation arm `feedback-guided` adds retrieved guidance to the repair prompt, so the value of the packs is measured against `feedback`.
- **Knowledge packs rebuilt**: retrieval is over `## ` sections instead of whole files (the three stub packs were ~690 bytes each), with headings weighted, identifiers split at underscores, and a **relevance floor** set from measured scores so that an irrelevant query returns nothing instead of "the first packs anyway". Eight new packs (Verilator warnings, synthesizable idioms, handshakes and FIFOs, CDC and resets, SDC, LibreLane variables, cocotb, formal) and a rewritten formal pack. The old formal pack told the model to use an `f_past_valid` register, which the typed-property guardrail rejects. `docs/knowledge-packs.md`.
- The RTL coder now receives relevant guidance for generation and repair (it used no packs before); the workspace agent has a read-only `knowledge` tool; user packs live in `<data dir>/skills/` and can override built-ins.
### Fixed (guardrails)
- **`SF-RTL-005` rejected correct RTL.** The static linter reported any `identifier <=` inside `always_comb` as a non-blocking assignment, so a less-or-equal comparison such as `if (count <= LIMIT)` was an ERROR the model could not repair (a local-model run failed `uart_tx` three times with unchanged code). `<=` now counts as an assignment only when it is the first assignment-like operator of a statement outside parentheses, with state carried across wrapped lines. Tests in the C++ suite fail against the old rule.
### Added (training data)
- Opt-in **trajectories** (`evals run --save-trajectories`): every graded sample with its full prompt and reply text, kept locally under `trajectories/` (gitignored). `evals export-sft` turns verified samples into chat-format training examples for a chosen split.
- A fixed **train/test split** of VerilogEval (`--split`), disjoint and stable under file order and exclusions, so a fine-tuned model is judged on problems it never saw.
- `VerilogEnv`: a Gymnasium-style environment (`reset`, `step`) over the same problems with a verifier-cascade reward and the testbench output withheld.
### Added (project flow)
- **`siliconforge flow`: your own RTL through the real flow, with no model involved.** A `project:` section in `siliconforge.yaml` (top, sources and globs, include directories, defines, clock, SDC, PDK, allowlisted LibreLane variables, waivers) drives Verilator lint, Yosys synthesis with an equivalence proof, and LibreLane place and route with signoff gates read from `metrics.json`: setup and hold, DRC, LVS, antenna, XOR, linter, max slew/capacitance/fanout, IR drop. Every gate is pass, fail or *not reported*; an absent metric is never a pass, and a stage that cannot run is an error, not a pass. Reports are JSON plus a self-contained HTML page with provenance (runner, tool versions, input fingerprint, config hash) and no absolute local paths; `flow diff` compares two. `docs/flow.md`.
- First real result: `pio16` (two source files and an include directory) on sky130A at 20 ns through WSL: lint clean, flattened equivalence proven (1,894 generic cells), timing met (setup +1.99 ns, hold +0.11 ns), DRC/LVS/antenna/XOR clean, GDSII produced, and 1,097 max-slew, 49 max-capacitance and 22 max-fanout violations, which fail the electrical gates. See `benchmarks/results/flow-pio16-sky130A-2026-11-21.json`.
### Fixed
- Equivalence checking of a design with a module hierarchy failed with `No SAT model available for cell ...`; both sides are flattened first, and the reported cell count is the whole design rather than the first module printed.
- `LibreLaneFlow.run` is split into `run` and `run_config`, so callers that need the raw metrics read the file the flow produced.
### Added (evals)
- **`siliconforge evals`**: a VerilogEval v2 (spec-to-RTL) harness with the unbiased pass@k estimator, bootstrap confidence intervals over problems, a paired `compare`, versioned result files (`siliconforge.eval/1`), and four arms (`single`, `feedback-plain`, `feedback`, `sampled`) so that what a loop buys can be separated from what its extra context buys. Arms see only the compile diagnostic of their own candidate; the scoring testbench is never exposed to them. `docs/evals.md`.
- `evals selfcheck`: grades every problem's own reference through the real grader. All 155 usable problems pass; `Prob099_m2014_q6c` is excluded because its testbench and prompt disagree with its reference (recorded in every result). The benchmark's testbenches needed one patch to compile on Icarus Verilog 14 (waveform-dump block), also recorded.
### Changed
- **License: AGPL-3.0-or-later** (was Apache-2.0 up to 0.3.0), with a commercial license available. SPDX headers on all source files, `THIRD_PARTY_NOTICES.md`, `docs/licensing.md`, `COMMERCIAL.md`, a contributor grant in `CONTRIBUTING.md`, and a "Source" link in the cockpit (AGPL section 13). CI checks headers and dependency licenses.
- Version 0.4.0 across the Python package and the desktop shell.
- **Installable wheel**: configs, example designs and the built web UI are bundled (`services/_resources`) and resolved through `paths.RESOURCE_ROOT`, so `pip install` works outside a source checkout.
### Fixed
- `llama-server` was started with a relative model path while running from its own directory, so the model could not be found; the path is now made absolute (this made the first GPU benchmark attempt fail).
### Added
- **Debug context for repair** (`services/orchestrator/debug`): a text waveform window around the failing step of a formal counterexample, the earliest cycle at which two traces differ, the source lines a lint error points at, and an attempt memory (what each earlier repair changed, as a diff, and what the verifier said afterwards) that is shown to the next repair. Before this, a repair saw the latest one-line diagnostic and nothing about earlier attempts. Switch: `pipeline.debug_context` (default on).
- **Verifier-ranked repair sampling**: `pipeline.repair_candidates: N` draws N repairs (the first at low temperature, the rest at 0.7) and keeps the one that passes the guardrails and Verilator lint; default 1 keeps the old behaviour.
- **Formal property IR is grounded inside the agent's own re-ask loop.** Signal fields are an `enum` of the contract's ports in the JSON schema, and a reply that uses an unknown signal (SVA keywords such as `cover` or `posedge`, helper names such as `f_past_valid`) is rejected with the allowed identifiers listed and the expression rules restated; the retry escalates the model tier. A first local-model run died at this stage on 3 of 4 designs.
- Agent tools `cone_of`, `waveform` and `trace_diff` (read-only, allowed in plan mode).
- Local-model benchmark results with failure traces: `benchmarks/results/local-Qwen3.5-9B-Q4_K_M-{debugctx,debugctx-irgrounding}-2026-11-21.json` (one run each, so read them as leads, not as a measured effect).
### Fixed
- **Cone-of-influence slicer rewritten**: the regex version ignored `if`/`case` conditions (so a register's enable was not in its cone), broke on multi-line statements and instance connections, and dropped every identifier that starts with "d" (`data`, `done`). The new one is a statement-level analysis with control dependencies; its limits are listed in the module docstring.
### Added
- **Tiny Tapeout (IHP SG13CMOS5L) mode**: `siliconforge tt scaffold|check|synth|gls|pack|doctor|harden`; info.yaml and port validation, tile budget from real post-synthesis area (Yosys against the IHP liberty), and gate-level simulation of your cocotb tests with the PDK cell models. `docs/tiny-tapeout.md`.
### Fixed
- **`WslRunner` executed command substitutions inside arguments** (`$(cmd)` and backticks were expanded by a shell layer even inside single quotes), so an argument or file name could run commands, and `$VAR` in arguments was silently emptied. Arguments are now escaped; covered by a unit test and a real WSL round-trip test.
- **ISA toolchain** from one `isa.yaml`: assembler/disassembler, Verilog decoder with a formal one-hot proof, golden model, timing checker (static proof + trace), docs with bit-field SVGs, and a standalone bundle. `docs/isa-toolchain.md`.
- **Verification toolkit**: mutation testing with Yosys equivalence filtering, cocotb runner with seeds, lockstep co-simulation and functional coverage, toggle coverage, independent UART/SPI/I2C/WS2812 decoders; `siliconforge verify sim|mutate|toggle`; MCP tools `isa_check`, `run_cocotb`, `mutation_test`. `docs/verification.md`.
- `hardware/isa/pio16`: a worked example (ISA, RTL, cocotb lockstep tests, committed generated files that a test keeps in sync).
- **Workspaces and a tool-using agent**: open any folder, run `siliconforge chat` / `agent` in it, or use the new API endpoints; permission modes (ask, accept-edits, auto, plan), sandboxed file tools, an allowlisted `eda` tool with provenance, undo, and instruction files (`SILICONFORGE.md` / `AGENTS.md`). See `docs/workspaces.md`.
- **Enforced model policy**: a workspace can restrict the models the router may call (`policy.allowed_models`); an empty chain is an error, not a fallback.
- **AI-usage ledger** and `siliconforge report ai-usage`: metadata-only record of every model call and a disclosure report.
- **Gemini 3.8 Flash via the Antigravity CLI** (`cli_agy`; profiles `gemini-agy`, `dev`, `competition`), opt-in like the other CLI bridges.
- **GPU guard**: checks free VRAM before starting a local server and refuses (with options) instead of evicting another program; `siliconforge models detect`; model discovery that reuses GGUF files already on disk.
- Native folder dialog bridge in the desktop shell (`pickFolder`).
- **Real EDA backends** (`native` / `wsl` / `docker`) running Yosys, SymbiYosys (BMC + PDR + cover), Verilator and LibreLane (OpenROAD) behind one interface; every result carries `mode` provenance and missing tools raise instead of silently mocking.
- Real LibreLane place-and-route with real GDSII, multi-corner PPA from `metrics.json`, EDA result cache, parallel RL episodes with early stopping.
- Typed property IR: `MUTUAL_EXCLUSION`, `COMBINATIONAL_EQUIVALENCE`, `LATENCY_BOUND`; pydantic schemas validating all structured LLM output with feedback-and-retry.
- Guardrail gates 5 (formal vacuity) and 6 (LEC) as real gates; port-width and expression-level signal grounding checks.
- Three new example designs (`up_down_counter`, `sync_fifo`, `uart_tx`) verified against real SymbiYosys (good RTL proven, injected bugs caught).
- CLI subcommands: `run`, `eval`, `doctor`, `serve`; event streaming and cancellation in the pipeline.
- Benchmarks: measured C++ telemetry vs Python (`benchmarks/results/`).
- Repository foundation: LICENSE, contribution/security docs, CI, cross-platform dev tasks, agent harness files.
### Changed
- `MockChipDesignLLM` (hard-coded design) replaced by `ReplayProvider` serving bundled fixtures; unknown specs now fail loudly.
- Simulated flows no longer write a fake GDSII file.
- C++ unit tests use an always-on `CHECK` (the previous `assert()` was compiled out in Release, so they passed vacuously) and parse real Yosys/OpenSTA formats.
### Removed
- Unused prototype Tcl/`.sby`/LibreLane config files under `eda/` (superseded by the real backends).
### Fixed
- `pip install -e .` failed on setuptools flat-layout discovery.
- `sf-telemetry.exe` was never found on Windows.
