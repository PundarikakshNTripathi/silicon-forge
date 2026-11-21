# AGENTS.md: SiliconForge

Canonical instructions for **all** coding agents (Claude Code, OpenAI Codex, Google Antigravity / Gemini CLI,
opencode, Pi / oh-my-pi, Cursor, Copilot). Other harness files are thin pointers to this one. Keep this file short
and accurate; update it in the same PR that changes a command or convention.

## What this is
SiliconForge turns a natural-language hardware spec into verified SystemVerilog, formal proofs and a physical
layout (GDSII) using open-source EDA (Yosys, SymbiYosys, Verilator, OpenROAD/LibreLane). A deterministic DAG
(`Spec -> DesignContract -> RTL -> guardrails -> formal/sim -> synthesis+LEC -> P&R + RL PPA -> signoff`) drives LLM
agents; **tool verdicts, not LLMs, decide every edge**.

## Commands (cross-platform; use these, not ad-hoc ones)
```bash
python scripts/dev.py doctor      # toolchain / GPU / EDA availability
python scripts/dev.py build-cpp   # CMake build of services/telemetry_engine (MSVC on Windows)
python scripts/dev.py test        # ctest + pytest
python scripts/dev.py lint        # ruff
python -m pytest -q tests/test_guardrails.py    # one file
siliconforge serve                # API + built UI at http://127.0.0.1:8000
cd services/dashboard && pnpm install && pnpm dev   # UI dev server :5173 (proxies /api to :8000)
siliconforge tui                  # terminal UI
siliconforge chat                 # interactive agent in the current folder (a workspace); `agent "task"` is headless
siliconforge init . --profile dev # write siliconforge.yaml (profile, backend, model policy)
siliconforge models detect        # local models + other model servers on this machine (read-only)
siliconforge isa check isa.yaml prog.asm    # ISA toolchain (gen / asm / disasm / check / run)
siliconforge verify mutate|sim|toggle ...   # mutation testing, cocotb seeds, toggle coverage (real backend only)
pytest -m real_eda                # real Yosys/SymbiYosys checks (auto-skipped without tools)
```
Python env: `uv venv --python 3.12 .venv && uv pip install -e ".[api,providers,tui,mcp,dev]"`. Python >= 3.11.

## Layout
- `services/orchestrator/` Python backend: `pipeline.py` (DAG), `workspace.py` + `agent/` (workspace agent), `agents/`, `guardrails/`, `eda/` (bridge, `backends/`
  simulated+real, `runner.py`, `librelane.py`, `formal_ir.py`, `vcd.py`), `inference/` (`router.py`, `providers/` incl. `agy`,
  `catalog.py`, `cache.py`, `llamacpp.py`, `gpu.py`, `ledger.py`), `isa/` (ISA toolchain), `verify/` (stdlib-only verification tools run via the EDA runner), `rl/` (PPA optimizer), `api/` (FastAPI, SQLite store, run manager),
  `skills/` (BM25 skill packs), `schemas.py` (pydantic IR), `paths.py` (resource vs data dir).
- `services/telemetry_engine/` C++20 log parser + static RTL linter (`sf-telemetry`), CMake.
- `services/dashboard/` React + Vite web UI (built to `dist/`, served by the API). `services/tui/` Textual TUI.
- `apps/desktop/` Electron shell (supervises a PyInstaller-frozen backend). Build: `python scripts/build_desktop.py`.
- `configs/` flow, PDK allowlists, system prompts, `models.yaml` (providers, prices, profiles).
- `hardware/examples/` designs: `spec.md`, `rtl/`, and `fixtures/` (contract, props, buggy RTL) for the replay provider.
- `benchmarks/` scripts + committed `results/*.json` (every README number cites one). `docs/` guides. `tests/` pytest.

## Rules that matter
1. **Honesty / provenance.** Every EDA result carries `mode: simulated|native|wsl|docker`. Never present simulated
   output as real, never fall back silently when a tool is missing: raise or label it.
2. **Guardrails are the product.** Changes under `services/orchestrator/guardrails/` need a test that fails without the change.
3. **Tests never call live LLM APIs.** Use the replay provider and the simulated backend; mark real-tool tests
   with `@pytest.mark.real_eda` (skipped when tools are absent).
4. **Secrets.** Never commit keys, `.env`, model weights (`*.gguf`), run outputs (`runs/`) or personal paths.
   Provider keys live in the OS keychain. The repo is **public**.
5. **Subscription CLI bridge** (`claude -p`, `codex exec`, `gemini -p`) is opt-in and must never read, copy or store OAuth tokens.
6. **Shared machine.** Never stop, evict or attach to another program's model server or GPU job (`inference/gpu.py` only reads). Tests never
   allocate large files (the dev laptop disk has filled up once from a test).
7. Prefer editing existing modules; keep prompts in `configs/prompts/`, PDK facts in `configs/pdks/`.

## Git workflow
Branch from `main` (`feat/*`, `fix/*`, `perf/*`, `docs/*`, `chore/*`), Conventional Commits, PR into `main`
(squash merge, CI green, review). Never force-push `main`. Do not add AI attribution trailers to commits or PRs.

## Definition of done
`python scripts/dev.py test` and `lint` pass; docs/CHANGELOG updated; UI changes verified in a browser; README
numbers trace to a committed benchmark artifact.
