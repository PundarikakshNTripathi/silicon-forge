# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guards around the agent loop: secrets, untrusted tool output, repeated calls, and permission rules.

Four independent pieces, each small enough to test on its own:

* `redact` removes credentials from text before it reaches the model's prompt or a saved session.
* `fence` wraps tool output in a marker and neutralises any attempt to close it, so a file that says "ignore your
  instructions" is presented to the model as data. This lowers the risk of prompt injection through file contents; it does
  not remove it, and the permission modes remain the real control.
* `LoopGuard` notices the same call with the same result coming back again and again.
* `Rules` are the workspace's `permissions:` allow and deny patterns.

Redaction is pattern-based and will miss a secret with an unusual shape; it is a safety net, not a guarantee.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------------------------------- redaction

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted private key]"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), "[redacted key]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "[redacted key]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "[redacted key]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"), "[redacted token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"), "[redacted token]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), "[redacted token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted key]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), "[redacted token]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{20,}"), r"\1 [redacted]"),
    # NAME=value or "name": "value" where the name says it is a secret
    (re.compile(r"""(?ix)\b([A-Z0-9_\-.]*(?:api[_-]?key|secret|token|passw(?:or)?d|credential)[A-Z0-9_\-.]*)(["']?\s*[=:]\s*)(["']?)[^\s"',;]{6,}\3"""),
     r"\1\2[redacted]"),
]


def redact(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


# ----------------------------------------------------------------------------------------------- untrusted data

_CLOSERS = re.compile(r"</\s*tool_output\s*>", re.I)


def fence(tool: str, text: str) -> str:
    """Present tool output as data. Any closing marker inside it is defanged so it cannot end the fence early."""
    safe = _CLOSERS.sub("</ tool_output>", text)
    return f'<tool_output tool="{tool}">\n{safe}\n</tool_output>'


# -------------------------------------------------------------------------------------------------- loop guard

@dataclass
class LoopGuard:
    """Counts consecutive identical (call, result) pairs. `warn_at` identical repeats add a note for the model; `stop_at`
    ends the turn. A different call or a different result resets the count, so a polling loop that makes progress is fine."""

    warn_at: int = 3
    stop_at: int = 5
    _last: str = ""
    _count: int = 0

    def observe(self, tool: str, args: dict[str, Any], result: str) -> str:
        """Returns "" (fine), "warn" or "stop"."""
        key = hashlib.sha256(json.dumps([tool, args, result], sort_keys=True, default=str).encode()).hexdigest()
        self._count = self._count + 1 if key == self._last else 1
        self._last = key
        if self._count >= self.stop_at:
            return "stop"
        return "warn" if self._count >= self.warn_at else ""

    def reset(self) -> None:
        self._last, self._count = "", 0


# ------------------------------------------------------------------------------------------- permission rules

_RULE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*(?:\((.*)\))?\s*$")


class RuleError(ValueError):
    pass


def subject(tool: str, args: dict[str, Any]) -> str:
    """The string a rule's pattern is matched against: the path for file tools, the program and arguments for `eda`."""
    if tool == "eda":
        return " ".join([str(args.get("tool", "")), *[str(a) for a in (args.get("args") or [])]]).strip()
    for key in ("path", "spec_path", "file", "test", "dump"):
        if key in args:
            # normalise so `rtl/../secret/x` and `.\secret\x` cannot slip past a pattern written as `secret/*`
            return posixpath.normpath(str(args[key]).replace(chr(92), "/"))
    return ""


@dataclass
class Rules:
    """`allow` and `deny` entries are `tool` or `tool(glob)`, e.g. `write_file(rtl/*)`, `eda(yosys *)`, `run_pipeline`.

    Deny wins over allow and applies in every mode, including `auto`. Allow skips the confirmation prompt that `ask` and
    `accept-edits` would show; it does not lift `plan` mode's read-only rule."""

    allow: list[tuple[str, str | None]] = field(default_factory=list)
    deny: list[tuple[str, str | None]] = field(default_factory=list)

    @classmethod
    def parse(cls, allow: list[str] | None = None, deny: list[str] | None = None) -> Rules:
        def one(raw: Any) -> tuple[str, str | None]:
            m = _RULE.match(str(raw))
            if not m:
                raise RuleError(f"bad permission rule {raw!r}; use `tool` or `tool(pattern)`")
            pat = m.group(2)
            return m.group(1), (pat.strip() if pat is not None else None)

        return cls([one(r) for r in allow or []], [one(r) for r in deny or []])

    @staticmethod
    def _hit(rules: list[tuple[str, str | None]], tool: str, subj: str) -> bool:
        return any(name == tool and (pat is None or fnmatch.fnmatchcase(subj, pat)) for name, pat in rules)

    def denied(self, tool: str, args: dict[str, Any]) -> bool:
        return self._hit(self.deny, tool, subject(tool, args))

    def allowed(self, tool: str, args: dict[str, Any]) -> bool:
        return self._hit(self.allow, tool, subject(tool, args))
