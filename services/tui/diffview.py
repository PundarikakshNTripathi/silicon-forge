# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an edit is about to change, as a unified diff, so an approval is made on the change and not on a JSON blob."""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

MAX_LINES = 80


def _split(text: str) -> list[str]:
    return text.splitlines()


def edit_diff(root: Path, tool: str, args: dict[str, Any]) -> list[str]:
    """Unified-diff lines for write_file / edit_file against what is on disk now; [] for any other tool or an unreadable target."""
    path = str(args.get("path", ""))
    if tool not in ("write_file", "edit_file") or not path:
        return []
    target = (root / path)
    try:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
    except (OSError, UnicodeDecodeError):
        return []
    if tool == "write_file":
        new = str(args.get("content", ""))
    else:
        old_snip, new_snip = str(args.get("old", "")), str(args.get("new", ""))
        if old_snip and old_snip in current:
            new = current.replace(old_snip, new_snip, 1)
        else:  # the edit will fail on its own terms; show what was asked for
            return [f"--- {path}", f"+++ {path}", *(f"-{ln}" for ln in _split(old_snip)), *(f"+{ln}" for ln in _split(new_snip))][:MAX_LINES]
    lines = list(difflib.unified_diff(_split(current), _split(new), f"a/{path}", f"b/{path}", lineterm="", n=2))
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + [f"... {len(lines) - MAX_LINES} more diff lines"]
    return lines


def counts(lines: list[str]) -> tuple[int, int]:
    """(added, removed) line counts of a unified diff."""
    add = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    rem = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    return add, rem


def compact(lines: list[str], context: int = 1, limit: int = 14) -> list[str]:
    """The changed lines of a unified diff with `context` unchanged lines around them; headers dropped, '...' where lines are skipped."""
    body = [ln for ln in lines if not ln.startswith(("---", "+++", "@@"))]
    keep: set[int] = set()
    for i, ln in enumerate(body):
        if ln.startswith(("+", "-")):
            keep.update(range(max(0, i - context), min(len(body), i + context + 1)))
    out: list[str] = []
    prev = -2
    for i in sorted(keep):
        if i != prev + 1 and out:
            out.append("  ...")
        out.append(body[i])
        prev = i
    return out[:limit] + ([f"  ... {len(out) - limit} more lines"] if len(out) > limit else [])


def pretty(tool: str, text: str, width: int = 150) -> list[str]:
    """A tool's JSON result as short readable lines. Falls back to the raw text when it is not the JSON these tools return."""
    import json

    try:
        data = json.loads(text)
    except ValueError:
        return text.splitlines()
    if not isinstance(data, dict):
        return text.splitlines()
    if tool == "list_dir" and isinstance(data.get("entries"), list):
        names = [str(e.get("name", e)) if isinstance(e, dict) else str(e) for e in data["entries"]]
        return ["  ".join(names)[:width]] if names else ["(empty)"]
    if tool == "read_file" and isinstance(data.get("content"), str):
        shown = data.get("shown", [1, data.get("total_lines", 0)])
        head = f"{data.get('path', '')}: lines {shown[0]}-{shown[1]} of {data.get('total_lines', '?')}"
        return [head, *data["content"].splitlines()]
    return [f"{k}: {json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v}"[:width] for k, v in data.items()]
