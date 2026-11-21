"""A conversational, tool-using agent session that works with any model the router can reach.

Local llama.cpp models and subscription CLIs do not all support native tool calling, so the protocol is one that every
backend can honour: each turn the model returns a JSON object `{"say": str, "tool": str | null, "args": object}`. A
null tool ends the turn and `say` is the reply. The schema is passed to the router, so llama.cpp and Antigravity enforce
it with constrained decoding; the parser still validates and asks the model to correct itself once on a malformed reply.

The transcript is rebuilt each step with older tool results compacted, so a 16k-context local model keeps working over
long sessions. Permissions follow Claude Code's modes: `ask` (confirm edits and commands), `accept-edits` (confirm
commands only), `auto` (no prompts), `plan` (read-only). Every model call goes through the router, so the workspace's
model policy and AI-usage ledger apply here exactly as they do to the pipeline.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from services.orchestrator.agent.tools import COMMAND, EDIT, READ, ToolBox, ToolError
from services.orchestrator.inference.providers.base import ProviderError
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.workspace import Workspace

Mode = Literal["ask", "accept-edits", "auto", "plan"]
MODES = ("ask", "accept-edits", "auto", "plan")
TASK = "agent_step"
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "say": {"type": "string"},
        "tool": {"type": ["string", "null"]},
        "args": {"type": "object"},
    },
    "required": ["say", "tool", "args"],
    "additionalProperties": False,
}
KEEP_FULL_RESULTS = 3  # most recent tool results shown in full; older ones are one-line summaries

SYSTEM = """You are SiliconForge's workspace agent: an expert digital-design engineer working inside one project folder.
You design, write, simulate, formally verify and analyse hardware (SystemVerilog, cocotb, SymbiYosys, Yosys).

How to work:
- Look before you act: list and read files first. Never invent file contents or tool output.
- Make small, verifiable steps. After writing RTL, lint it and run the real tools; trust tool results over your own opinion.
- A tool result with provenance 'simulated' is not real; say so. If a tool is missing or a check fails, report it plainly.
- You only see this folder. Paths are relative to it.

Reply with ONE JSON object every turn, nothing else:
{{"say": "<short note to the user>", "tool": "<tool name or null>", "args": {{...}}}}
Use tool=null with your final answer in "say" when the task is done or you need the user's input.

Tools:
{tools}
{instructions}"""


@dataclass
class AgentEvent:
    kind: str  # assistant | tool_call | tool_result | approval | error | done
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    reply: str
    steps: int
    stopped: str  # done | step_limit | denied | error | budget


Approver = Callable[[str, str, dict], bool]  # (permission class, summary, details) -> allowed


def parse_action(text: str) -> dict:
    """Parse the model's reply into an action dict; tolerant of code fences and surrounding prose."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.DOTALL)
    if fence:
        t = fence.group(1)
    elif not t.startswith("{"):
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            t = t[start:end + 1]
    obj = json.loads(t)
    if not isinstance(obj, dict) or "say" not in obj:
        raise ValueError("reply must be a JSON object with 'say', 'tool' and 'args'")
    obj.setdefault("tool", None)
    obj["args"] = obj.get("args") or {}
    if obj["tool"] in ("", "null", "none"):
        obj["tool"] = None
    if not isinstance(obj["args"], dict):
        raise ValueError("'args' must be an object")
    return obj


class AgentSession:
    def __init__(self, router: ModelRouter, ws: Workspace, tools: ToolBox, *, mode: Mode = "ask",
                 approver: Approver | None = None, on_event: Callable[[AgentEvent], None] | None = None,
                 max_steps: int = 14, result_chars: int = 3500) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.router, self.ws, self.tools = router, ws, tools
        self.mode: Mode = mode
        self.approver = approver
        self.on_event = on_event or (lambda e: None)
        self.max_steps = max_steps
        self.result_chars = result_chars
        #: transcript entries: {"role": user|assistant|tool, ...}
        self.history: list[dict[str, Any]] = []

    # ---------------------------------------------------------------------------------------- permissions

    def _allowed(self, spec_permission: str, name: str, args: dict) -> tuple[bool, str]:
        if spec_permission == READ:
            return True, ""
        if self.mode == "plan":
            return False, "plan mode is read-only: describe the change instead of making it"
        needs_approval = (self.mode == "ask" and spec_permission in (EDIT, COMMAND)) or (
            self.mode == "accept-edits" and spec_permission == COMMAND)
        if not needs_approval:
            return True, ""
        if self.approver is None:
            return False, "this needs the user's approval and no one is available to give it"
        summary = f"{name} {json.dumps(args)[:300]}"
        ok = self.approver(spec_permission, summary, {"tool": name, "args": args})
        return (True, "") if ok else (False, "the user declined this action")

    # -------------------------------------------------------------------------------------------- prompt

    def _system(self) -> str:
        text, src = self.ws.instructions()
        instr = f"\nProject instructions from {src} (follow them):\n{text}\n" if text else ""
        return SYSTEM.format(tools=self.tools.describe(), instructions=instr)

    def _transcript(self) -> str:
        results = [i for i, h in enumerate(self.history) if h["role"] == "tool"]
        recent = set(results[-KEEP_FULL_RESULTS:])
        parts = []
        for i, h in enumerate(self.history):
            if h["role"] == "user":
                parts.append(f"USER: {h['text']}")
            elif h["role"] == "assistant":
                parts.append("ASSISTANT: " + json.dumps({"say": h["say"], "tool": h["tool"], "args": h["args"]}))
            else:
                body = h["text"] if i in recent else f"[earlier result: {h['summary']}]"
                parts.append(f"TOOL RESULT ({h['name']}): {body}")
        return "\n".join(parts) + "\nASSISTANT:"

    # ----------------------------------------------------------------------------------------------- turn

    def send(self, message: str) -> TurnResult:
        self.history.append({"role": "user", "text": message})
        corrected = False
        for step in range(1, self.max_steps + 1):
            try:
                resp = self.router.complete("AGENT", TASK, self._system(), self._transcript(),
                                            temperature=0.2, bypass_cache=True, json_schema=ACTION_SCHEMA)
            except ProviderError as exc:
                self._emit("error", f"model call failed: {exc}")
                return TurnResult(f"Model call failed: {exc}", step, "error")
            except Exception as exc:  # noqa: BLE001 - budget errors etc. must surface, not crash the UI
                self._emit("error", f"{type(exc).__name__}: {exc}")
                return TurnResult(str(exc), step, "budget" if "udget" in type(exc).__name__ else "error")
            try:
                act = parse_action(resp.content)
            except (ValueError, json.JSONDecodeError) as exc:
                if corrected:
                    self._emit("error", f"model returned an invalid action twice: {exc}")
                    return TurnResult("The model could not produce a valid action.", step, "error")
                corrected = True
                self.history.append({"role": "tool", "name": "protocol", "summary": "invalid reply",
                                     "text": f"Your reply was not valid ({exc}). Reply with exactly one JSON object "
                                             'of the form {"say": ..., "tool": ..., "args": {...}}.'})
                continue
            corrected = False
            self.history.append({"role": "assistant", "say": act["say"], "tool": act["tool"], "args": act["args"]})
            if act["say"]:
                self._emit("assistant", act["say"])
            if act["tool"] is None:
                self._emit("done", act["say"])
                return TurnResult(act["say"], step, "done")
            denied = self._run_tool(act["tool"], act["args"])
            if denied:
                return TurnResult(f"Stopped: {denied}", step, "denied")
        self._emit("error", f"step limit ({self.max_steps}) reached")
        return TurnResult("Reached the step limit for this turn. Say 'continue' to keep going.", self.max_steps,
                          "step_limit")

    def _run_tool(self, name: str, args: dict) -> str | None:
        spec = self.tools.by_name().get(name)
        self._emit("tool_call", f"{name} {json.dumps(args)[:200]}", {"tool": name, "args": args})
        if spec is None:
            self._tool_result(name, f"error: unknown tool '{name}'. Available: {', '.join(self.tools.by_name())}")
            return None
        ok, why = self._allowed(spec.permission, name, args)
        if not ok:
            self._emit("approval", f"{name}: {why}", {"tool": name, "denied": True})
            self._tool_result(name, f"denied: {why}")
            return why if why.startswith("the user declined") else None
        try:
            out = self.tools.call(name, args)
            text = json.dumps(out, ensure_ascii=False) if not isinstance(out, str) else out
        except ToolError as exc:
            text = f"error: {exc}"
        except Exception as exc:  # noqa: BLE001 - a tool crash goes to the model as an error, never kills the session
            text = f"error: {type(exc).__name__}: {exc}"
        self._tool_result(name, text)
        return None

    def _tool_result(self, name: str, text: str) -> None:
        clipped = text if len(text) <= self.result_chars else text[: self.result_chars // 2] + " ...[truncated]... " + text[-self.result_chars // 2:]
        first = clipped.replace("\n", " ")[:90]
        self.history.append({"role": "tool", "name": name, "text": clipped, "summary": f"{name} -> {first}"})
        self._emit("tool_result", clipped[:600], {"tool": name})

    def _emit(self, kind: str, text: str = "", data: dict | None = None) -> None:
        self.on_event(AgentEvent(kind, text, data or {}))
