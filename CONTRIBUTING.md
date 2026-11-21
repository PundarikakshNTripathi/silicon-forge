# Contributing to SiliconForge

Thanks for helping! This project follows a standard team workflow so it stays healthy as it grows.

## Workflow
1. **Open or pick an issue** first for anything non-trivial.
2. **Branch from `main`**: `feat/<topic>`, `fix/<topic>`, `perf/<topic>`, `docs/<topic>`, `chore/<topic>`.
3. **Commit with [Conventional Commits](https://www.conventionalcommits.org/)**: `feat(router): add fallback chain`.
4. **Open a PR into `main`** using the template. CI must be green and one review is required. PRs are **squash-merged**; `main` stays linear and always releasable.
5. Releases are tagged `vMAJOR.MINOR.PATCH` (SemVer); notes live in `CHANGELOG.md`.

## Dev setup
```bash
uv venv --python 3.12 .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e ".[api,providers,tui,mcp,dev]"
python scripts/dev.py build-cpp     # CMake build of sf-telemetry (MSVC on Windows)
python scripts/dev.py test          # C++ ctest + Python pytest
python scripts/dev.py lint
python scripts/dev.py doctor        # shows which EDA tools / GPU are available
```
The UI lives in `services/dashboard` (`pnpm install && pnpm dev`).

## Ground rules
- **Honesty first**: results from the simulated backend must never be presented as real EDA output. Every result carries its provenance (`simulated | native | wsl | docker`).
- **No secrets, keys, model weights or personal paths** in commits. Keys go in your OS keychain or `.env` (git-ignored).
- Add tests with your change. LLM-dependent tests use the replay provider, never live APIs.
- Guardrail changes (`services/orchestrator/guardrails/`) need a test that fails without the change.

## License and contributor grant
SiliconForge is AGPL-3.0-or-later, and the maintainer also offers it under a commercial license ([COMMERCIAL.md](COMMERCIAL.md)).
By opening a pull request you confirm that you wrote the change (or have the right to submit it) and you license it to the
project under AGPL-3.0-or-later and also grant the maintainer a perpetual, worldwide, non-exclusive right to sublicense
your contribution under other terms, including commercial ones. Sign off commits with `git commit -s` (Developer
Certificate of Origin). If you cannot agree to this, say so in the PR before it is reviewed.

## Coding agents
Agent harness files (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, ...) are checked in. `AGENTS.md` is the single source of truth; keep the others as thin pointers.
