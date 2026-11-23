# The static demo

A copy of the web cockpit that needs no backend, no install and no sign-in: the same React app, built with `VITE_DEMO_MODE=true`
and fed by static files. It exists so that someone can look at SiliconForge in a browser for a minute. It is deployed to GitHub Pages
by `.github/workflows/demo-pages.yml` (`https://<owner>.github.io/silicon-forge/`).

## What is real in it

Nothing in the demo is produced by demo-only code. Each part comes from something in this repository:

| Part | Where it comes from |
| :--- | :--- |
| Evals, AgentBench, Insights, Models, Knowledge pages | The committed files in `benchmarks/results/`, served through the real `/api/benchmarks`, `/api/skills` and `/api/estimate` route code. The agent-insights panel is the real `telemetry.aggregate` over the AgentBench runs of our own loop. |
| Runs | Two finished runs on real tools (WSL: Yosys, SymbiYosys, OpenROAD; sky130A), stored sanitised in `benchmarks/demo/runs/`. They go through the real run store and routes, so they carry their provenance stamp, events, formal retry, equivalence check, PPA episodes and layout. |
| Agent replay | A recording made by `services/orchestrator/demo/record_trace.py`. The agent session, tool box, permission and approval code, loop guard, transcript compaction and the static linter are the real ones. **The language model is a script of fixed replies**: no model is called, and nothing about model quality is shown. The page says so. |
| Approve / Request revision | Both outcomes were recorded from the real agent (a decline ends the turn, so the revision is a second message). The visitor chooses which recording to follow; nothing is generated. |
| Machine details (GPU, installed tools, local models, keys) | Replaced by neutral values. The bundle is identical on every machine, and a test fails if a path or user name gets into it. |

Writes do nothing: starting a run opens the recorded run, saving a setting says it was not saved.

## Rebuild it

```bash
python scripts/export_demo_bundle.py          # services/dashboard/public/demo-data and demo-assets
cd services/dashboard
pnpm install
pnpm run build:demo-static                    # tsc + vite build --mode demo  (reads .env.demo)
pnpm exec vite preview --mode demo            # look at it
```

`pnpm run build:demo` does the first and last steps together. `python scripts/export_demo_bundle.py --check` fails if the committed
bundle is out of date, and so does `tests/test_demo_bundle.py`; commit the regenerated bundle with the change that caused it.

To serve it from a sub-path set `VITE_BASE` (no slashes: `VITE_BASE=silicon-forge`). The demo routes on the URL hash, so a deep link
cannot 404 on a static host. `vercel.json` and `public/_redirects` are there for hosts that prefer clean paths.

## Show a new run or a new result

- **New result file:** commit it under `benchmarks/results/` and re-export.
- **New recorded run:** make a real run, then `python scripts/export_demo_bundle.py --capture runs/<run_id> <name>` (sanitises it into
  `benchmarks/demo/runs/<name>.json`; refuses if local details remain), then re-export. Layout images come from `assets/layout/<module>.png`.
- **A different recording:** edit the scripted replies in `record_trace.py`, which also holds the notes on what is real.

## The engineering note

`services/dashboard/src/demo/engineeringNote.md` is the author's own text. The repository ships headings and `[AUTHOR TO WRITE BY HAND]`
placeholders; the export and the Pages workflow warn while any remain. Nothing here generates or edits that prose.

## Deploy

GitHub Pages: Settings, Pages, Source: GitHub Actions; then run the workflow or push to `main`. Vercel or Cloudflare Pages: root
`services/dashboard`, build command `pnpm run build:demo-static`, output `dist`, no environment variables needed.
