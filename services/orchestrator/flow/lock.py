# SPDX-License-Identifier: AGPL-3.0-or-later
"""`flow.lock`: the tool versions a project was last signed off with, so a different Yosys or LibreLane is noticed, not assumed.

The lock pins tools and the PDK name only. It does not pin the RTL: that changes with every edit and the report's input
fingerprint already identifies it. `flow run --locked` refuses to start when an installed tool differs from the lock, and a tool
that is missing never matches (a lock written without the tool cannot be satisfied by an absent one either).

Not covered: the PDK's own revision (sky130A and others are installed outside the flow's tool set) and OpenROAD's build
inside LibreLane beyond what `librelane --version` prints.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

SCHEMA = "siliconforge.flow.lock/1"
LOCK_NAME = "flow.lock"


class LockError(ValueError):
    pass


def make_lock(tools: dict[str, str], pdk: str, top: str) -> dict[str, Any]:
    missing = sorted(t for t, v in tools.items() if v in ("", "not found", "unknown"))
    if missing:
        raise LockError(f"cannot lock: tool version unavailable for {', '.join(missing)}")
    return {"schema": SCHEMA, "tools": dict(sorted(tools.items())), "pdk": pdk, "top": top,
            "locked": time.strftime("%Y-%m-%d")}


def write_lock(root: Path, lock: dict[str, Any]) -> Path:
    path = root / LOCK_NAME
    path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_lock(root: Path) -> dict[str, Any]:
    path = root / LOCK_NAME
    if not path.is_file():
        raise LockError(f"no {LOCK_NAME} in {root} (create one with: siliconforge flow lock)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LockError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("tools"), dict):
        raise LockError(f"{path} is not a {SCHEMA} file")
    return data


def mismatches(lock: dict[str, Any], tools: dict[str, str], pdk: str) -> list[str]:
    """Human-readable differences between the lock and what is installed now; empty means the lock holds."""
    out: list[str] = []
    for name, want in lock["tools"].items():
        have = tools.get(name, "not found")
        if have != want:
            out.append(f"{name}: locked {want!r}, installed {have!r}")
    for name in sorted(set(tools) - set(lock["tools"])):
        out.append(f"{name}: installed {tools[name]!r} but not in the lock (run `flow lock` to add it)")
    if lock.get("pdk") != pdk:
        out.append(f"pdk: locked {lock.get('pdk')!r}, project uses {pdk!r}")
    return out
