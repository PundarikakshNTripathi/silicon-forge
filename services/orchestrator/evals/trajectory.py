# SPDX-License-Identifier: AGPL-3.0-or-later
"""Trajectories: what the model was asked, what it answered and what the grader said, kept locally for later training.

Opt-in (`--save-trajectories`). Unlike the AI-usage ledger, which records only hashes, a trajectory contains the full prompt
and reply text, so it lives under the data directory (`trajectories/`, gitignored) and is never committed or uploaded. The
format is one JSON object per graded sample.

`export_sft` turns verified samples into chat-format training examples. Only samples the grader passed are exported, and the
problem split decides which problems may be trained on, so a model fine-tuned on the export is judged on problems it never saw.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from services.orchestrator.paths import data_root

SCHEMA = "siliconforge.trajectory/1"


def default_path(tag: str = "") -> Path:
    name = f"traj-{time.strftime('%Y%m%d-%H%M%S')}{('-' + tag) if tag else ''}.jsonl"
    return data_root() / "trajectories" / name


class TrajectoryWriter:
    """Append-only JSONL, safe to share between worker threads."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.count = 0

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps({"schema": SCHEMA, "ts": round(time.time(), 3), **record}, ensure_ascii=False)
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
            self.count += 1


def read(paths: Iterable[Path | str]) -> Iterator[dict[str, Any]]:
    for p in paths:
        with Path(p).open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("schema") == SCHEMA:
                    yield rec


def export_sft(paths: Iterable[Path | str], out: Path, allowed_problems: set[str] | None = None, per_problem: int = 2,
               min_completion_chars: int = 20) -> dict[str, Any]:
    """Write verified samples as `{"messages": [system, user, assistant]}` lines.

    * only samples with `graded.passed` and a final answer are used;
    * `allowed_problems` restricts to a training split (None means no restriction: say so in the caller);
    * at most `per_problem` distinct answers per problem, so easy problems do not dominate;
    * the assistant turn is the code block the grader scored, not the raw reply with its reasoning.
    """
    seen: dict[str, set[str]] = {}
    total = kept = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for rec in read(paths):
            total += 1
            pid = rec.get("problem", "")
            g = rec.get("graded") or {}
            code = (rec.get("rtl") or "").strip()
            calls = rec.get("calls") or []
            if not g.get("passed") or len(code) < min_completion_chars or not calls:
                continue
            if allowed_problems is not None and pid not in allowed_problems:
                continue
            bucket = seen.setdefault(pid, set())
            if code in bucket or len(bucket) >= per_problem:
                continue
            bucket.add(code)
            first = calls[0]
            msgs = [{"role": "system", "content": first["system"]}, {"role": "user", "content": first["user"]},
                    {"role": "assistant", "content": f"```verilog\n{code}\n```"}]
            f.write(json.dumps({"messages": msgs, "problem": pid, "model": rec.get("model")}, ensure_ascii=False) + "\n")
            kept += 1
    return {"samples_read": total, "examples_written": kept, "problems": len(seen), "out": str(out)}
