# SPDX-License-Identifier: AGPL-3.0-or-later
"""A small span recorder for agent runs: who did what, for how long, and how it ended.

Spans nest as `run > step > llm | tool`. Each has a monotonic-clock duration (immune to wall-clock jumps) and a wall-clock
start (so spans from different processes line up). Spans carry **metadata only**: tool names, error classes, token counts,
cost. Never prompt text, tool arguments or tool output, so a trace is as safe to keep or share as the usage ledger.

Standard library only. `telemetry/otel.py` maps spans to OpenTelemetry GenAI conventions for export when the optional SDK
is installed; `telemetry/metrics.py` turns spans into the latency, tool-success, cost and recovery figures.
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

KINDS = ("run", "step", "llm", "tool")
_CURRENT: contextvars.ContextVar[Span | None] = contextvars.ContextVar("sf_current_span", default=None)


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    kind: str
    start: float  # wall clock, seconds since the epoch
    duration_s: float = 0.0
    status: str = "ok"  # ok | error
    error: str = ""  # error class when status == "error" (see metrics.POLICY_ERRORS for the non-failures)
    attrs: dict[str, Any] = field(default_factory=dict)

    def fail(self, error: str, **attrs: Any) -> None:
        self.status, self.error = "error", error
        self.attrs.update(attrs)

    def set(self, **attrs: Any) -> None:
        self.attrs.update(attrs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Span:
        return cls(trace_id=d["trace_id"], span_id=d["span_id"], parent_id=d.get("parent_id"), name=d["name"], kind=d["kind"],
                   start=float(d["start"]), duration_s=float(d.get("duration_s", 0.0)), status=d.get("status", "ok"),
                   error=d.get("error", ""), attrs=dict(d.get("attrs") or {}))


Sink = Callable[[Span], None]


class JsonlSink:
    """Appends each finished span as one JSON line. Thread-safe; a crash can leave a torn last line, which readers skip."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def __call__(self, span: Span) -> None:
        line = json.dumps(span.to_dict(), ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def read_spans(path: str | Path) -> list[Span]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[Span] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(Span.from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return out


class Tracer:
    """Records spans. `span()` is a context manager; the innermost open span becomes the parent of the next one, per thread
    or task (a `ContextVar`), so nested calls need no plumbing. Exceptions leaving a span mark it as an error and propagate."""

    def __init__(self, sinks: list[Sink] | None = None, clock: Callable[[], float] = time.time,
                 perf: Callable[[], float] = time.perf_counter) -> None:
        self.sinks: list[Sink] = list(sinks or [])
        self.spans: list[Span] = []
        self._clock, self._perf = clock, perf
        self._lock = threading.Lock()

    @contextmanager
    def span(self, name: str, kind: str, **attrs: Any) -> Generator[Span, None, None]:
        if kind not in KINDS:
            raise ValueError(f"span kind must be one of {KINDS}, got {kind!r}")
        parent = _CURRENT.get()
        sp = Span(trace_id=parent.trace_id if parent else uuid.uuid4().hex[:16], span_id=uuid.uuid4().hex[:12],
                  parent_id=parent.span_id if parent else None, name=name, kind=kind, start=self._clock(), attrs=dict(attrs))
        token = _CURRENT.set(sp)
        t0 = self._perf()
        try:
            yield sp
        except BaseException as exc:
            if sp.status == "ok":
                sp.fail(type(exc).__name__)
            raise
        finally:
            sp.duration_s = round(self._perf() - t0, 6)
            _CURRENT.reset(token)
            self._finish(sp)

    def _finish(self, sp: Span) -> None:
        with self._lock:
            self.spans.append(sp)
        for sink in self.sinks:
            try:
                sink(sp)
            except Exception:  # noqa: BLE001, S110 - observability must never break the thing it observes
                pass

    def current(self) -> Span | None:
        return _CURRENT.get()


class NullTracer(Tracer):
    """Does nothing, cheaply: the default when tracing is not requested."""

    @contextmanager
    def span(self, name: str, kind: str, **attrs: Any) -> Generator[Span, None, None]:
        yield Span(trace_id="", span_id="", parent_id=None, name=name, kind=kind, start=0.0, attrs=dict(attrs))
