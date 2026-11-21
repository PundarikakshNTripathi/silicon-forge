# SPDX-License-Identifier: AGPL-3.0-or-later
"""Long-term project memory: a short, plain-text file the agent reads at the start of every session and can add to.

Opt-in (`memory: true` in siliconforge.yaml, or the file already exists), local (`.siliconforge/memory.md` in the workspace),
redacted (anything that looks like a secret is masked before it is written), bounded (the newest notes win a fixed prompt budget),
and user-editable: it is Markdown bullets, so a person can open it, fix a note, or delete the file.
"""
from __future__ import annotations

import re
from pathlib import Path

from services.orchestrator.agent.safety import redact

MAX_PROMPT_CHARS = 2400
MAX_NOTES = 60
MAX_NOTE_CHARS = 240


class Memory:
    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "memory.md"

    def notes(self) -> list[str]:
        if not self.path.is_file():
            return []
        return [ln[2:].strip() for ln in self.path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.startswith("- ")]

    def add(self, note: str) -> str:
        """Save one note and return exactly what was written. Newlines collapse to spaces; secrets are masked; a repeat is not saved twice."""
        clean = re.sub(r"\s+", " ", redact(note)).strip()[:MAX_NOTE_CHARS]
        if not clean:
            raise ValueError("an empty note cannot be saved")
        notes = self.notes()
        if clean in notes:
            return clean
        notes = (notes + [clean])[-MAX_NOTES:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("# Project memory (edit freely; the agent reads this at the start of every session)\n\n"
                             + "\n".join(f"- {n}" for n in notes) + "\n", encoding="utf-8")
        return clean

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def prompt_block(self) -> str:
        """What goes into the system prompt: the newest notes that fit the budget, oldest first."""
        kept: list[str] = []
        used = 0
        for n in reversed(self.notes()):
            if used + len(n) + 3 > MAX_PROMPT_CHARS:
                break
            kept.append(n)
            used += len(n) + 3
        if not kept:
            return ""
        return "\nProject memory (notes saved from earlier sessions; treat them as hints, and trust the files over a note):\n" + \
            "\n".join(f"- {n}" for n in reversed(kept)) + "\n"
