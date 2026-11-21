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
| `isa_check`, `isa_gen` | read / edit | assemble a program and prove its pin timing; generate decoder, golden model and docs from `isa.yaml` ([isa-toolchain](isa-toolchain.md)) |
| `sim`, `mutate` | command | cocotb with Icarus for chosen seeds; mutation testing of a test suite ([verification](verification.md)) |
| `tt_check`, `tt_synth`, `tt_gls` | read / command | Tiny Tapeout preflight, real IHP synthesis and tile budget, gate-level simulation ([tiny-tapeout](tiny-tapeout.md)) |
| `run_pipeline` | command | the fixed pipeline: contract, RTL, guardrails, formal, LEC, layout |

Permission modes follow Claude Code: **ask** confirms edits and commands, **accept-edits** confirms commands only,
**auto** asks nothing, **plan** is read-only.

Limits you should know about:

- File tools cannot leave the workspace, and the agent cannot write `siliconforge.yaml`, `.siliconforge/` or `.git/`, so
  it cannot lift its own model policy or edit its own usage ledger.
- `eda` is not a sandbox: `make`, `pytest` and `python3` run arbitrary code. That is why it is a command that asks first,
  and why `auto` is for workspaces you trust.
- With the `simulated` backend the `eda` tool refuses to run rather than invent results.

## Permission rules

The four modes decide what asks for confirmation. For finer control, add rules to `siliconforge.yaml` (the agent cannot edit that
file, so it cannot loosen its own rules):

```yaml
permissions:
  allow:
    - write_file(rtl/*)          # no prompt for edits under rtl/, even in `ask` mode
    - eda(yosys *)               # nor for yosys runs
    - run_pipeline
  deny:
    - read_file(secrets/*)       # refused in every mode, reads included
    - write_file(rtl/locked/*)
    - eda(rm *)
```

A rule is a tool name, or `tool(glob)`. The glob is matched against the path (file tools) or `program arguments...` (`eda`).
Paths are normalised first, so `rtl/../secrets/x` and `.\secrets\x` match `secrets/*`. **Deny wins over allow and applies in
`auto` mode.** Allow only removes the prompt; it does not lift `plan` mode's read-only rule. A malformed rule is an error when
the agent starts, not something silently skipped.

## Hooks

Hooks run a command of yours before or after a tool call, for checks that must happen every time and should not depend on the
model remembering:

```yaml
hooks:
  - event: post_tool
    match: write_file(rtl/*)       # same `tool` / `tool(glob)` syntax as the permission rules
    run: [python, scripts/style_check.py]
    timeout: 60                    # seconds, default 60, at most 600
  - event: pre_tool
    match: eda(openroad *)
    run: [python, scripts/require_clean_lint.py]
```

- `pre_tool` runs before the call. A non-zero exit **blocks** it and the hook's output goes to the model as the reason. A hook
  that cannot start, or times out, blocks too: a safety check that did not run is not a pass. The first blocking hook decides.
- `post_tool` runs after a call that succeeded; its output is appended to the result the model sees (a style report on the file
  it just wrote, for example). A failing post hook adds its exit status and output; it does not undo the call.
- The command is an argument list run without a shell, in the workspace folder, with `SF_HOOK_EVENT`, `SF_TOOL`, `SF_ARGS` (JSON),
  `SF_SUBJECT` (the normalised path or command) and, for post hooks, `SF_RESULT_HEAD` in its environment.
- Hooks live in `siliconforge.yaml`, which the agent cannot write, so it cannot add, remove or edit one. They run with your own
  privileges: treat a manifest you did not write as you would a Makefile.

## Guards inside the loop

- **Context budget.** The transcript is kept under `context_chars` (36,000 characters, about 9k tokens). When a session grows past
  it, older turns are replaced by a short digest built by code (what was asked, which files were read and changed, which commands
  ran, how many calls failed); the last two user turns stay verbatim and the saved session keeps everything. Nothing here calls a
  model, so it costs no tokens. What is lost is old tool output; the agent re-reads a file if it needs it.
- **Loop guard.** Three identical calls with identical results add a note telling the model to change approach; the fifth ends the
  turn as `loop`. Any different call or result resets the count.
- **Untrusted output.** Tool results are wrapped in `<tool_output>` tags, a closing tag inside them is defanged, and the system
  prompt says to treat the content as data. This reduces prompt-injection risk from file contents; it does not remove it, so keep
  `ask` mode for repositories you did not write.
- **Redaction.** API keys, tokens, private keys, bearer headers and values of variables named like secrets are replaced with
  `[redacted ...]` in tool output before the model sees it and in saved sessions. It is pattern-based and will miss a secret with
  an unusual shape; a secret you paste into your own message is sent to the model as typed.

## Saved conversations

Nothing is saved by default. `siliconforge chat --save` (or `agent --save "task"`) writes the conversation to
`.siliconforge/sessions/<id>.jsonl`, one JSON entry per line, in clear text: your prompts, the model's replies and tool
output, including file contents the agent read. The folder is inside the workspace state directory, which is gitignored
and which the agent itself cannot write to.

```bash
siliconforge sessions                    # list saved conversations in this folder
siliconforge chat --resume               # continue the newest one
siliconforge chat --resume 20261121-183000
```

A resumed session sends the model the same transcript it had before (older tool results are still shortened to one line, as in
a live session). File changes made by the earlier session are not rolled back by resuming, and `/undo` only knows about edits
made since the process started. A line cut off by a killed process is skipped when loading.

## From the API and the desktop app

`POST /api/workspaces/open` opens or creates a workspace; `GET /api/fs` is a directories-only browser for the web UI
(the desktop app uses the native folder dialog through `window.siliconforgeDesktop.pickFolder()`).
`POST /api/agent/sessions` starts a session, messages stream back over `/events` or `/stream`, and an approval request
blocks the agent until `POST .../approvals/{id}` answers it (or ten minutes pass, which counts as a decline).
