# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hooks: commands the workspace owner runs before or after the agent's tool calls.

```yaml
hooks:
  - event: post_tool
    match: write_file(rtl/*)          # same `tool` / `tool(glob)` syntax as permission rules
    run: [python, scripts/check_style.py]
    timeout: 60
  - event: pre_tool
    match: eda(openroad *)
    run: [python, scripts/not_before_lint.py]
```

* `pre_tool`: runs before the call. A non-zero exit **blocks** the call and the hook's output goes back to the model as the
  reason. A hook that cannot start or times out also blocks (a safety check that did not run is not a pass).
* `post_tool`: runs after the call; its output is appended to the tool result, so the model sees, for example, the style check
  of the file it just wrote. A failing post hook does not undo the call; it adds its output and an exit-status note.

The command is an argument list run without a shell, in the workspace folder. The call is described to it through environment
variables (`SF_HOOK_EVENT`, `SF_TOOL`, `SF_ARGS` as JSON, `SF_SUBJECT`, and for post hooks `SF_RESULT_HEAD`). Hooks are defined in
`siliconforge.yaml`, which the agent cannot write, so it cannot add or remove one. They run with the user's own privileges and
are code the user chose to run; treat a manifest you did not write as you would a Makefile.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.orchestrator.agent.safety import Rules, subject

EVENTS = ("pre_tool", "post_tool")
_MATCH = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:\((.*)\))?\s*$")
MAX_OUTPUT = 1500


class HookError(ValueError):
    pass


@dataclass(frozen=True)
class Hook:
    event: str
    tool: str
    pattern: str | None
    argv: tuple[str, ...]
    timeout: float = 60.0

    def matches(self, tool: str, args: dict[str, Any]) -> bool:
        return Rules._hit([(self.tool, self.pattern)], tool, subject(tool, args))


@dataclass
class HookOutcome:
    blocked: bool = False
    output: str = ""


@dataclass
class HookSet:
    hooks: list[Hook] = field(default_factory=list)

    @classmethod
    def parse(cls, raw: list[dict[str, Any]] | None) -> HookSet:
        hooks: list[Hook] = []
        for i, item in enumerate(raw or []):
            if not isinstance(item, dict):
                raise HookError(f"hooks[{i}]: expected a mapping")
            event = item.get("event")
            if event not in EVENTS:
                raise HookError(f"hooks[{i}]: event must be one of {', '.join(EVENTS)}")
            m = _MATCH.match(str(item.get("match", "")))
            if not m:
                raise HookError(f"hooks[{i}]: match must be `tool` or `tool(pattern)`")
            run = item.get("run")
            if not isinstance(run, list) or not run or not all(isinstance(a, str) for a in run):
                raise HookError(f"hooks[{i}]: run must be a non-empty list of strings (an argument list, not a shell line)")
            try:
                timeout = float(item.get("timeout", 60))
            except (TypeError, ValueError) as exc:
                raise HookError(f"hooks[{i}]: timeout must be a number of seconds") from exc
            if not 0 < timeout <= 600:
                raise HookError(f"hooks[{i}]: timeout must be between 0 and 600 seconds")
            hooks.append(Hook(event, m.group(1), (m.group(2) or "").strip() if m.group(2) is not None else None,
                              tuple(run), timeout))
        return cls(hooks)

    def run(self, event: str, tool: str, args: dict[str, Any], root: Path, result: str = "") -> HookOutcome:
        out = HookOutcome()
        for hook in self.hooks:
            if hook.event != event or not hook.matches(tool, args):
                continue
            env = {**os.environ, "SF_HOOK_EVENT": event, "SF_TOOL": tool, "SF_ARGS": json.dumps(args),
                   "SF_SUBJECT": subject(tool, args), "SF_RESULT_HEAD": result[:2000]}
            label = " ".join(hook.argv[:2])
            try:
                p = subprocess.run(list(hook.argv), cwd=root, env=env, capture_output=True, text=True,
                                   timeout=hook.timeout, check=False)
                text = ((p.stdout or "") + (p.stderr or "")).strip()[:MAX_OUTPUT]
                failed = p.returncode != 0
                note = f"hook `{label}` exited {p.returncode}" if failed else ""
            except subprocess.TimeoutExpired:
                text, failed, note = "", True, f"hook `{label}` timed out after {hook.timeout:g}s"
            except OSError as exc:
                text, failed, note = "", True, f"hook `{label}` could not start: {exc}"
            piece = "\n".join(s for s in (note, text) if s)
            if piece:
                out.output += ("\n" if out.output else "") + piece
            if failed and event == "pre_tool":
                out.blocked = True
                break  # the first blocking hook decides; later hooks would not change the outcome
        return out
