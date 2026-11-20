"""Minimal VCD (Value Change Dump) reader used to distill formal counterexamples and drive the waveform viewer."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VcdTrace:
    timescale: str = ""
    signals: dict[str, int] = field(default_factory=dict)  # name -> width
    changes: dict[str, list[tuple[int, str]]] = field(default_factory=dict)  # name -> [(time, value)]
    end_time: int = 0

    def value_at(self, name: str, time: int) -> str | None:
        last: str | None = None
        for t, v in self.changes.get(name, []):
            if t > time:
                break
            last = v
        return last

    def snapshot(self, time: int) -> dict[str, str]:
        return {n: v for n in self.signals if (v := self.value_at(n, time)) is not None}

    def to_wavejson(self, max_signals: int = 32) -> dict:
        """Compact JSON for the UI waveform viewer."""
        return {
            "timescale": self.timescale,
            "end": self.end_time,
            "signals": [
                {"name": n, "width": self.signals[n], "changes": self.changes.get(n, [])}
                for n in list(self.signals)[:max_signals]
            ],
        }


def parse_vcd(text: str, top_scope_only: bool = True) -> VcdTrace:
    """Parse a VCD file. With `top_scope_only`, only the top module's signals are kept."""
    trace = VcdTrace()
    id_to_name: dict[str, str] = {}
    scope: list[str] = []
    time = 0
    in_defs = True
    tokens = text.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if in_defs:
            if tok == "$timescale":
                j = tokens.index("$end", i)
                trace.timescale = " ".join(tokens[i + 1 : j])
                i = j
            elif tok == "$scope":
                scope.append(tokens[i + 2])
                i += 3
            elif tok == "$upscope":
                if scope:
                    scope.pop()
            elif tok == "$var":
                width = int(tokens[i + 2])
                ident = tokens[i + 3]
                name = tokens[i + 4]
                if not top_scope_only or len(scope) <= 1:
                    id_to_name.setdefault(ident, name)
                    trace.signals.setdefault(name, width)
                    trace.changes.setdefault(name, [])
                i = tokens.index("$end", i)
            elif tok == "$enddefinitions":
                in_defs = False
                i = tokens.index("$end", i)
        else:
            if tok.startswith("#"):
                time = int(tok[1:])
                trace.end_time = max(trace.end_time, time)
            elif tok[0] in "01xXzZ" and len(tok) > 1:
                name = id_to_name.get(tok[1:])
                if name:
                    trace.changes[name].append((time, tok[0]))
            elif tok[0] in "bB" and i + 1 < len(tokens):
                name = id_to_name.get(tokens[i + 1])
                if name:
                    trace.changes[name].append((time, tok[1:]))
                i += 1
        i += 1
    return trace


def to_int(value: str | None) -> int | None:
    """Binary string -> int, or None when it contains x/z."""
    if value is None or any(c in value for c in "xXzZ"):
        return None
    return int(value, 2)
