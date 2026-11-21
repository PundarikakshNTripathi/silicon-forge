# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the transcript the model sees, and keep it inside a budget however long the session runs.

The full history is never altered (it is what a saved session stores). Each step, `build` renders it in stages and stops at
the first stage that fits the character budget:

1. older tool results become one-line summaries; the newest `keep_full` stay whole;
2. every earlier turn is replaced by a short, deterministic *digest* (what was asked, which files were read and changed, which
   commands ran), keeping the last `keep_turns` user turns verbatim;
3. all remaining tool results in those turns shrink to summaries.

The digest is built by code, not by a model: it costs no tokens, cannot hallucinate, and cannot fail. What it loses is the
detail of old tool output; the model can read a file again if it needs it. Characters stand in for tokens (about four to one
for code and prose), which is close enough to size a budget and needs no tokenizer.
"""
from __future__ import annotations

import json
from typing import Any

from services.orchestrator.agent.safety import fence

READ_TOOLS = {"read_file", "list_dir", "grep", "lint_rtl", "cone_of", "waveform", "trace_diff", "knowledge", "isa_check", "tt_check"}
WRITE_TOOLS = {"write_file", "edit_file", "isa_gen"}
RUN_TOOLS = {"eda", "run_pipeline", "sim", "mutate", "tt_synth", "tt_gls"}


def _render(entries: list[dict[str, Any]], keep_full: int, shrink_all: bool = False) -> list[str]:
    results = [i for i, h in enumerate(entries) if h["role"] == "tool"]
    recent = set() if shrink_all else set(results[-keep_full:])
    parts: list[str] = []
    for i, h in enumerate(entries):
        if h["role"] == "user":
            parts.append(f"USER: {h['text']}")
        elif h["role"] == "assistant":
            parts.append("ASSISTANT: " + json.dumps({"say": h["say"], "tool": h["tool"], "args": h["args"]}))
        else:
            body = fence(h["name"], h["text"]) if i in recent else f"[earlier result: {h['summary']}]"
            parts.append(f"TOOL RESULT ({h['name']}): {body}")
    return parts


def digest(entries: list[dict[str, Any]]) -> str:
    """What happened in `entries`, in a few lines."""
    asked: list[str] = []
    read: list[str] = []
    changed: list[str] = []
    ran: list[str] = []
    errors = 0
    for h in entries:
        if h["role"] == "user":
            asked.append(h["text"].replace("\n", " ")[:140])
        elif h["role"] == "assistant" and h.get("tool"):
            tool, args = h["tool"], h.get("args") or {}
            target = str(args.get("path") or args.get("spec_path") or args.get("tool") or "")
            if tool in READ_TOOLS and target and target not in read:
                read.append(target)
            elif tool in WRITE_TOOLS and target and target not in changed:
                changed.append(target)
            elif tool in RUN_TOOLS:
                ran.append(f"{tool} {target}".strip())
        elif h["role"] == "tool" and str(h.get("summary", "")).split("->", 1)[-1].strip().lower().startswith(("error", "denied")):
            errors += 1
    lines = ["EARLIER IN THIS SESSION (condensed; read a file again if you need its contents):"]
    lines += [f"- user asked: {a}" for a in asked[-6:]]
    if len(asked) > 6:
        lines.append(f"- ...and {len(asked) - 6} earlier request(s)")
    if read:
        lines.append("- read: " + ", ".join(read[-12:]) + (f" (+{len(read) - 12} more)" if len(read) > 12 else ""))
    if changed:
        lines.append("- changed: " + ", ".join(changed[-12:]) + (f" (+{len(changed) - 12} more)" if len(changed) > 12 else ""))
    if ran:
        lines.append("- ran: " + "; ".join(ran[-8:]) + (f" (+{len(ran) - 8} more)" if len(ran) > 8 else ""))
    if errors:
        lines.append(f"- {errors} tool call(s) returned an error or were denied")
    return "\n".join(lines)


def build(history: list[dict[str, Any]], keep_full: int = 3, budget_chars: int = 36000, keep_turns: int = 2) -> tuple[str, str]:
    """(transcript ending in "ASSISTANT:", stage) where stage is "full", "summaries", "digest" or "digest+shrunk"."""
    parts = _render(history, keep_full)
    text = "\n".join(parts)
    if len(text) <= budget_chars:
        return text + "\nASSISTANT:", "full" if keep_full >= sum(h["role"] == "tool" for h in history) else "summaries"

    user_idx = [i for i, h in enumerate(history) if h["role"] == "user"]
    start = user_idx[-keep_turns] if len(user_idx) >= keep_turns else (user_idx[0] if user_idx else 0)
    head, tail = history[:start], history[start:]
    prefix = digest(head) + "\n" if head else ""
    text = prefix + "\n".join(_render(tail, keep_full))
    if len(text) <= budget_chars:
        return text + "\nASSISTANT:", "digest"
    text = prefix + "\n".join(_render(tail, keep_full, shrink_all=True))
    return text + "\nASSISTANT:", "digest+shrunk"
