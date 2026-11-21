# SPDX-License-Identifier: AGPL-3.0-or-later
"""The single tool surface every benchmark arm uses.

`InstrumentedToolBox` is a `ToolBox` that applies, in one place: the task's tool allowlist, deterministic fault injection, a
wall-clock deadline for the run, a `tool` span per call (the *same* span shape the agent session uses, so the same metric code
reads it) and an event log the verifiers can inspect. Our own `AgentSession` receives it as its toolbox; the LangGraph and
Pydantic AI arms call `call_text`, which returns exactly what our session would put in front of the model for that call (errors
as `error: ...`, results clipped the same way). The tools are therefore identical across arms; only the loop around them differs.

`tool_json_schema` turns a `ToolSpec`'s `name -> "type, meaning"` parameter map into a JSON schema, which is what the frameworks
show the model.
"""
from __future__ import annotations

import inspect
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from services.orchestrator.agent.safety import fence, redact
from services.orchestrator.agent.session import tool_error_class
from services.orchestrator.agent.tools import ToolBox, ToolError, ToolSpec
from services.orchestrator.agentbench.faults import FaultInjector
from services.orchestrator.telemetry import NullTracer, Span, Tracer

RESULT_CHARS = 3500  # the same clip AgentSession applies to a tool result before the model sees it

_TYPES = {"string": "string", "str": "string", "integer": "integer", "int": "integer", "number": "number", "float": "number",
          "boolean": "boolean", "bool": "boolean", "object": "object", "dict": "object", "list": "array", "array": "array"}


def tool_json_schema(spec: ToolSpec) -> dict[str, Any]:
    """JSON schema for a tool's parameters. Types come from the description; whether a parameter is required comes from the
    function's own signature (a parameter with a default is optional), which is the authority the description can lag behind."""
    try:
        sig = inspect.signature(spec.fn).parameters
    except (TypeError, ValueError):
        sig = {}
    props: dict[str, Any] = {}
    required: list[str] = []
    for name, desc in spec.params.items():
        head = desc.split(",", 1)[0].strip().lower()
        word = re.split(r"[\s/]", head, maxsplit=1)[0]
        typ = _TYPES.get(word, "string")
        prop: dict[str, Any] = {"type": typ, "description": desc}
        if typ == "array":
            prop["items"] = {"type": "string"}
        props[name] = prop
        has_default = (sig[name].default is not inspect.Parameter.empty) if name in sig else (
            "default" in desc.lower() or "optional" in desc.lower())
        if not has_default:
            required.append(name)
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def clip(text: str, limit: int = RESULT_CHARS) -> str:
    return text if len(text) <= limit else text[: limit // 2] + " ...[truncated]... " + text[-limit // 2:]


@dataclass
class ToolEvent:
    name: str
    args: dict[str, Any]
    ok: bool
    error: str = ""
    result_chars: int = 0
    t: float = 0.0


@dataclass
class InstrumentedToolBox(ToolBox):
    tracer: Tracer = field(default_factory=NullTracer)
    allowed: tuple[str, ...] = ()
    injector: FaultInjector = field(default_factory=FaultInjector)
    events: list[ToolEvent] = field(default_factory=list)
    run_span: Span | None = None  # the run these calls belong to; explicit because a framework may call from another thread
    deadline: float | None = None  # time.monotonic() value after which every call fails
    guarded: bool = False  # redact secrets and fence output, as AgentSession does; off for a plain framework arm

    def specs(self) -> list[ToolSpec]:
        base = super().specs()
        return [s for s in base if s.name in self.allowed] if self.allowed else base

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if self.allowed and name not in self.allowed:
            raise ToolError(f"Unknown tool '{name}'. Available: {', '.join(self.allowed)}")
        with self.tracer.span(name, "tool", parent=self.run_span) as sp:
            try:
                if self.deadline is not None and time.monotonic() > self.deadline:
                    sp.fail("timeout")
                    raise ToolError("this run's time limit has been reached")
                injected = self.injector.should_fail(name)
                if injected:
                    raise ToolError(injected)
                out = super().call(name, args)
            except ToolError as exc:
                if sp.status == "ok":
                    sp.fail(tool_error_class(exc))
                self.events.append(ToolEvent(name, dict(args), False, sp.error, 0, time.time()))
                raise
            except Exception:
                sp.fail("crash")
                self.events.append(ToolEvent(name, dict(args), False, "crash", 0, time.time()))
                raise
            text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
            sp.set(result_chars=len(text))
            self.events.append(ToolEvent(name, dict(args), True, "", len(text), time.time()))
            return out

    def call_text(self, name: str, args: dict[str, Any]) -> str:
        """What a framework should hand back to the model for this call: the result, or `error: ...`, clipped and (when
        `guarded`) redacted and fenced exactly like AgentSession does."""
        try:
            out = self.call(name, args)
        except ToolError as exc:
            text = f"error: {exc}"
        except Exception as exc:  # noqa: BLE001 - a tool crash is a result for the model, never the end of the run
            text = f"error: {type(exc).__name__}: {exc}"
        else:
            text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
        text = clip(text)
        return fence(name, redact(text)) if self.guarded else text

    def attempted(self, *names: str) -> list[ToolEvent]:
        return [e for e in self.events if e.name in names]
