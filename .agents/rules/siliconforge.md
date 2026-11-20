# SiliconForge always-on rules (Antigravity)
- Read `/AGENTS.md` before changing code.
- Simulated EDA output must always be labelled `simulated`; never fall back to mocks silently.
- Never commit secrets, `.env`, `*.gguf`, `runs/`, or personal paths: this repository is public.
- Run `python scripts/dev.py test` and `python scripts/dev.py lint` before proposing a PR.
