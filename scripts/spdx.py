#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add or verify the SPDX license header on first-party source files.

    python scripts/spdx.py            # add missing headers
    python scripts/spdx.py --check    # exit 1 and list files that lack one (used in CI)

Generated artifacts (hardware/**/gen, build output, node_modules) are not touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = "SPDX-License-Identifier: AGPL-3.0-or-later"
SKIP_PARTS = {"node_modules", "dist", "backend-dist", ".venv", "build", "__pycache__", ".git", "hardware", "models", "runs",
              "out", "_deps", "cmake-build"}
COMMENT = {".py": "#", ".ts": "//", ".tsx": "//", ".js": "//", ".cpp": "//", ".h": "//", ".hpp": "//", ".cmake": "#"}
ROOTS = ["services", "scripts", "benchmarks", "tests", "apps/desktop"]


def candidates() -> list[Path]:
    out = []
    for r in ROOTS:
        base = ROOT / r
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.suffix in COMMENT and p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_PARTS):
                out.append(p)
    return sorted(out)


def has_tag(text: str) -> bool:
    return TAG in "\n".join(text.splitlines()[:6])


def add(path: Path) -> bool:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    if has_tag(text):
        return False
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    header = f"{COMMENT[path.suffix]} {TAG}"
    at = 1 if lines and lines[0].startswith("#!") else 0
    lines.insert(at, header)
    path.write_bytes(nl.join(lines).encode("utf-8"))
    return True


def main() -> int:
    check = "--check" in sys.argv
    missing = [p for p in candidates() if not has_tag(p.read_text(encoding="utf-8"))]
    if check:
        for p in missing:
            print(f"missing SPDX header: {p.relative_to(ROOT)}")
        return 1 if missing else 0
    for p in missing:
        add(p)
    print(f"added headers to {len(missing)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
