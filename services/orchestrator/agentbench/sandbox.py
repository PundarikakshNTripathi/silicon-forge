# SPDX-License-Identifier: AGPL-3.0-or-later
"""A throwaway workspace per run, and a way to say exactly what the agent changed in it.

Every run gets a fresh temp folder built from the task's fixture, so runs cannot leak into each other and a verifier can compare
the folder before and after. The agent never sees the verifier, which lives in this package and not in the workspace.
"""
from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from services.orchestrator.workspace import STATE_DIR, Workspace, init_workspace

_SKIP_DIRS = {STATE_DIR, ".git", "__pycache__"}
_SKIP_FILES = {".gitignore"}


def write_files(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def snapshot(root: Path) -> dict[str, str]:
    """{relative path: sha256} of every file the agent could have touched (the state dir and git metadata are ignored)."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or set(rel.parts) & _SKIP_DIRS or p.name in _SKIP_FILES:
            continue
        out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@dataclass(frozen=True)
class Diff:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()

    @property
    def any(self) -> bool:
        return bool(self.added or self.removed or self.modified)

    def touched(self, prefix: str) -> list[str]:
        return [p for p in (*self.added, *self.removed, *self.modified) if p.startswith(prefix)]


def diff(before: dict[str, str], after: dict[str, str]) -> Diff:
    return Diff(added=tuple(sorted(set(after) - set(before))), removed=tuple(sorted(set(before) - set(after))),
                modified=tuple(sorted(p for p in set(before) & set(after) if before[p] != after[p])))


@dataclass
class Sandbox:
    workspace: Workspace
    before: dict[str, str] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.workspace.root

    def changes(self) -> Diff:
        return diff(self.before, snapshot(self.root))

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8", errors="replace")


@contextmanager
def sandbox(files: dict[str, str], backend: str = "simulated", manifest_extra: dict | None = None) -> Iterator[Sandbox]:
    """Build the fixture in a temp folder; the folder is removed afterwards. `before` is the snapshot after the build."""
    with tempfile.TemporaryDirectory(prefix="sf_agentbench_", ignore_cleanup_errors=True) as tmp:
        ws = init_workspace(Path(tmp) / "work", profile="dev", backend=backend)
        write_files(ws.root, files)
        if manifest_extra:
            import yaml

            manifest = ws.root / "siliconforge.yaml"
            doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            doc.update(manifest_extra)
            manifest.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            from services.orchestrator.workspace import load_workspace

            ws = load_workspace(ws.root)
        yield Sandbox(ws, snapshot(ws.root))
