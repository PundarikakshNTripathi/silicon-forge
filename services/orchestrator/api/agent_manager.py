# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agent sessions for the GUI: one worker thread per turn, an event log to stream, and blocking approvals.

The agent loop is synchronous. When it needs the user's permission it parks on a `threading.Event` until the UI answers
through the API (or a timeout passes, which counts as a decline), so the browser/desktop app drives the same
permission model the terminal does.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from services.orchestrator.agent import AgentEvent, AgentSession, create_session
from services.orchestrator.agent.session import MODES
from services.orchestrator.telemetry import Tracer
from services.orchestrator.workspace import load_workspace, remember_workspace

APPROVAL_TIMEOUT_S = 600.0


class SessionBusyError(RuntimeError):
    pass


@dataclass
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    allow: bool = False


@dataclass
class ManagedSession:
    id: str
    session: AgentSession
    workspace: str
    events: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[str, _Pending] = field(default_factory=dict)
    busy: bool = False
    last_result: dict[str, Any] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, kind: str, text: str = "", data: dict | None = None) -> None:
        with self.lock:
            self.events.append({"seq": len(self.events), "ts": time.time(), "kind": kind, "text": text,
                                "data": data or {}})

    def since(self, after: int) -> list[dict[str, Any]]:
        with self.lock:
            return self.events[after + 1:]


class AgentManager:
    def __init__(self, approval_timeout_s: float = APPROVAL_TIMEOUT_S, store: Any = None) -> None:
        self.sessions: dict[str, ManagedSession] = {}
        self.approval_timeout_s = approval_timeout_s
        #: when given, every agent session records spans (latency, tool success, tokens, cost; no content) into this Store
        self.store = store

    # ------------------------------------------------------------------------------------------ lifecycle

    def create(self, workspace: str, profile: str | None = None, mode: str = "ask", **session_kw: Any) -> ManagedSession:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        ws = load_workspace(workspace)
        remember_workspace(ws)
        sid = uuid.uuid4().hex[:10]
        holder: dict[str, ManagedSession] = {}

        def on_event(ev: AgentEvent) -> None:
            holder["m"].add(ev.kind, ev.text, ev.data)

        def approver(kind: str, summary: str, details: dict) -> bool:
            m = holder["m"]
            aid = uuid.uuid4().hex[:8]
            pending = _Pending()
            m.pending[aid] = pending
            m.add("approval_request", summary, {"id": aid, "permission": kind, **details})
            answered = pending.event.wait(self.approval_timeout_s)
            m.pending.pop(aid, None)
            m.add("approval_result", "allowed" if (answered and pending.allow) else "declined", {"id": aid})
            return answered and pending.allow

        if self.store is not None and "tracer" not in session_kw:
            store = self.store
            session_kw["tracer"] = Tracer(sinks=[lambda span: store.add_span(span, session=sid)])
        session = create_session(ws, profile=profile, mode=mode, approver=approver, on_event=on_event, **session_kw)  # type: ignore[arg-type]
        holder["m"] = m = ManagedSession(sid, session, str(ws.root))
        self.sessions[sid] = m
        return m

    def get(self, sid: str) -> ManagedSession:
        m = self.sessions.get(sid)
        if m is None:
            raise KeyError(sid)
        return m

    def close(self, sid: str) -> None:
        m = self.sessions.pop(sid, None)
        if m:
            for p in m.pending.values():  # release any thread waiting on a decision
                p.event.set()
            m.session.close()  # stops MCP server subprocesses, if the workspace configured any

    # ---------------------------------------------------------------------------------------------- turns

    def send(self, sid: str, text: str) -> None:
        m = self.get(sid)
        with m.lock:
            if m.busy:
                raise SessionBusyError("the agent is still working on the previous message")
            m.busy = True
        m.add("user", text)

        def work() -> None:
            try:
                res = m.session.send(text)
                m.last_result = {"reply": res.reply, "steps": res.steps, "stopped": res.stopped}
                m.add("turn_end", res.reply, m.last_result)
            except Exception as exc:  # noqa: BLE001 - a crashed turn must reach the UI
                m.add("error", f"{type(exc).__name__}: {exc}")
                m.add("turn_end", "", {"stopped": "error"})
            finally:
                m.busy = False

        threading.Thread(target=work, daemon=True, name=f"sf-agent-{sid}").start()

    def answer(self, sid: str, approval_id: str, allow: bool) -> bool:
        p = self.get(sid).pending.get(approval_id)
        if p is None:
            return False
        p.allow = allow
        p.event.set()
        return True

    def set_mode(self, sid: str, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.get(sid).session.mode = mode  # type: ignore[assignment]
        self.get(sid).add("mode", mode)

    def undo(self, sid: str) -> str:
        m = self.get(sid)
        msg = m.session.tools.undo()
        m.add("undo", msg)
        return msg
