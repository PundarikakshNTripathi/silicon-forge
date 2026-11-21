# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression triage: group failing test runs by what went wrong, so a hundred red seeds read as three causes.

Input is the `sim.json` that `sf_cocotb_run.py` writes (one or more files, each with a list of seeds and tests). A failure's
*signature* is its message with the parts that vary between seeds removed: numbers, hex and binary values, quoted strings and
instruction counters. A lockstep divergence is reduced to the *set of fields that disagree*, which is usually the better key
than the values. Two failures with the same signature are very likely the same bug; the same signature can still hide two
bugs, and different signatures can be one bug seen through two symptoms, so the buckets are a reading aid, not a verdict.

Every bucket keeps the seeds that hit it, the tests, one full example message, and the replay command for the lowest seed.
Crashed simulations (no test results at all) get their own bucket so they are never counted as passes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DIVERGE = re.compile(r"diverge after instruction #\d+")
_FIELD = re.compile(r"^\s{2}([A-Za-z_][\w.\[\]]*): rtl=.* model=", re.M)
_TAIL = re.compile(r"\nrecent instructions.*", re.S)
_QUOTED = re.compile(r"(['\"]).*?\1")
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9]+'[hHbBdD][0-9a-fA-FxXzZ_]+\b")
_BITS = re.compile(r"\b[01xXzZ]{4,}\b")
_NUM = re.compile(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?")


@dataclass
class Bucket:
    signature: str
    failures: int = 0
    seeds: list[int] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    example: str = ""
    sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"signature": self.signature, "failures": self.failures, "seeds": sorted(set(self.seeds)),
                "tests": sorted(set(self.tests)), "example": self.example, "sources": sorted(set(self.sources))}


def signature(message: str) -> str:
    """The message with everything that varies from run to run taken out."""
    msg = message.strip()
    if _DIVERGE.search(msg):
        fields = sorted(set(_FIELD.findall(msg)))
        return "lockstep divergence on " + (", ".join(fields) if fields else "unknown fields")
    msg = _TAIL.sub("", msg)
    first = next((ln for ln in msg.splitlines() if ln.strip()), "")
    first = _HEX.sub("H", first)  # before quotes: the apostrophe in 4'b1010 is not a string delimiter
    first = _QUOTED.sub("S", first)
    first = _BITS.sub("B", first)
    first = _NUM.sub("N", first)
    first = re.sub(r"\s+", " ", first).strip()
    return first[:160] or "(failure with no message)"


def _read(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a sim.json report")
    if "error" in data and "runs" not in data and "seeds" not in data:
        raise ValueError(f"{path}: the simulation did not run ({data['error']})")
    return data


def triage(reports: list[Path]) -> dict[str, Any]:
    """Bucket every failing test (and crashed run) across the reports, largest bucket first."""
    buckets: dict[str, Bucket] = {}
    total = passed = 0
    for path in reports:
        data = _read(path)
        for run in data.get("runs", []):
            seed = int(run.get("seed", 0))
            if run.get("crashed") and not run.get("tests"):
                total += 1
                b = buckets.setdefault("crash: " + signature(run["crashed"]), Bucket("crash: " + signature(run["crashed"])))
                b.failures += 1
                b.seeds.append(seed)
                b.example = b.example or run["crashed"]
                b.sources.append(path.name)
                continue
            for t in run.get("tests", []):
                if t.get("status") == "skip":
                    continue
                total += 1
                if t.get("status") == "pass":
                    passed += 1
                    continue
                sig = signature(t.get("message", ""))
                b = buckets.setdefault(sig, Bucket(sig))
                b.failures += 1
                b.seeds.append(seed)
                b.tests.append(t.get("name", "?"))
                b.example = b.example or t.get("message", "")
                b.sources.append(path.name)
    ordered = sorted(buckets.values(), key=lambda b: (-b.failures, b.signature))
    return {"schema": "siliconforge.triage/1", "reports": [p.name for p in reports], "ran": total, "passed": passed,
            "failed": total - passed, "buckets": [b.as_dict() for b in ordered]}


def render(result: dict[str, Any], replay: str | None = None) -> str:
    lines = [f"{result['failed']} failing of {result['ran']} run(s) in {len(result['reports'])} report(s): "
             f"{len(result['buckets'])} distinct signature(s)"]
    for i, b in enumerate(result["buckets"], 1):
        lines.append(f"\n[{i}] {b['failures']}x  {b['signature']}")
        lines.append(f"    seeds {b['seeds']}   tests {', '.join(b['tests']) or '-'}")
        if replay and b["seeds"]:
            lines.append(f"    replay: {replay.replace('{seed}', str(b['seeds'][0]))}")
        ex = b["example"].strip().splitlines()
        lines.extend(f"    | {ln}" for ln in ex[:4])
    return "\n".join(lines)
