#!/usr/bin/env python3
"""CI check: agent harness files stay thin pointers to AGENTS.md and referenced commands exist."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []

agents = ROOT / "AGENTS.md"
if not agents.exists():
    errors.append("AGENTS.md is missing")
else:
    text = agents.read_text(encoding="utf-8")
    tasks = set(re.findall(r"scripts/dev\.py ([a-z-]+)", text))
    dev = (ROOT / "scripts" / "dev.py").read_text(encoding="utf-8")
    for t in tasks:
        if f'"{t}"' not in dev:
            errors.append(f"AGENTS.md references unknown dev task: {t}")

pointers = {
    "CLAUDE.md": "@AGENTS.md",
    "GEMINI.md": "@AGENTS.md",
    ".omp/AGENTS.md": "AGENTS.md",
    ".cursor/rules/siliconforge.mdc": "AGENTS.md",
    ".github/copilot-instructions.md": "AGENTS.md",
}
for rel, needle in pointers.items():
    p = ROOT / rel
    if not p.exists():
        errors.append(f"missing harness file: {rel}")
    elif needle not in p.read_text(encoding="utf-8"):
        errors.append(f"{rel} must reference {needle}")

for skill in (ROOT / ".agents" / "skills").glob("*/SKILL.md"):
    head = skill.read_text(encoding="utf-8").split("---")
    if len(head) < 3 or "name:" not in head[1] or "description:" not in head[1]:
        errors.append(f"{skill.relative_to(ROOT)} needs name/description frontmatter")

if errors:
    print("\n".join(f"harness check: {e}" for e in errors))
    sys.exit(1)
print("harness check: ok")
