# Workspaces and the agent

A **workspace** is the folder the agent works in. Open one in the desktop app, or run SiliconForge inside it from a
terminal, the same way you would start Claude Code or Codex in a project.

```bash
cd my-chip
siliconforge init . --profile dev --backend wsl        # optional: writes siliconforge.yaml
siliconforge chat                                      # interactive session in this folder
siliconforge agent --mode auto "add a synchronous reset to rtl/cnt.sv and prove it"   # one task, headless
```

Without a `siliconforge.yaml` the folder still works, with defaults, and nothing is written until you ask.

## `siliconforge.yaml`

```yaml
name: pemu
profile: competition          # routing profile from configs/models.yaml
backend: wsl                  # simulated | native | wsl | docker
pdk: ihp-sg13cmos5l
policy:
  allowed_models: ["gemini-3.8-flash-*"]   # globs over `model` and `provider:model`; enforced by the router
  max_budget_usd: 0
```

Runs, caches, the agent's undo history and the AI-usage ledger live under `.siliconforge/` (ignored by git).
Standing instructions go in `SILICONFORGE.md`, falling back to `AGENTS.md`, and are added to every prompt.

## What the agent can do

| Tool | Class | Notes |
|---|---|---|
| `list_dir`, `read_file`, `grep`, `lint_rtl` | read | always allowed |
| `write_file`, `edit_file` | edit | exact, unique-match edits; every change is undoable (`/undo`) |
| `eda` | command | an allowlist (`iverilog`, `vvp`, `verilator`, `yosys`, `sby`, `eqy`, `mcy`, `make`, `pytest`, `python3`) as an argument list in the workspace; the result carries its provenance (`wsl`, `native`, `docker`) |
| `run_pipeline` | command | the fixed pipeline: contract, RTL, guardrails, formal, LEC, layout |

Permission modes follow Claude Code: **ask** confirms edits and commands, **accept-edits** confirms commands only,
**auto** asks nothing, **plan** is read-only.

Limits you should know about:

- File tools cannot leave the workspace, and the agent cannot write `siliconforge.yaml`, `.siliconforge/` or `.git/`, so
  it cannot lift its own model policy or edit its own usage ledger.
- `eda` is not a sandbox: `make`, `pytest` and `python3` run arbitrary code. That is why it is a command that asks first,
  and why `auto` is for workspaces you trust.
- With the `simulated` backend the `eda` tool refuses to run rather than invent results.

## From the API and the desktop app

`POST /api/workspaces/open` opens or creates a workspace; `GET /api/fs` is a directories-only browser for the web UI
(the desktop app uses the native folder dialog through `window.siliconforgeDesktop.pickFolder()`).
`POST /api/agent/sessions` starts a session, messages stream back over `/events` or `/stream`, and an approval request
blocks the agent until `POST .../approvals/{id}` answers it (or ten minutes pass, which counts as a decline).
