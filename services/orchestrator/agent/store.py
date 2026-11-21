# SPDX-License-Identifier: AGPL-3.0-or-later
"""Saved agent conversations, one JSON-lines file per session under `<workspace>/.siliconforge/sessions/`.

Opt-in (`--save` / `--resume`): the file holds prompts, replies and tool output in clear text, so it is never written unless the
user asks. It stays inside the workspace's state directory, which `siliconforge init` adds to `.gitignore`.

Each line is one history entry exactly as `AgentSession.history` holds it, so a resumed session continues with the same
transcript the model saw before. A torn last line (the process was killed mid-write) is skipped on load.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[0-9]{8}-[0-9]{6}(-[a-z0-9]+)?$")


@dataclass(frozen=True)
class SessionInfo:
    id: str
    path: Path
    turns: int
    first_message: str
    modified: float


class SessionStore:
    def __init__(self, directory: Path) -> None:
        self.dir = directory

    def new_id(self) -> str:
        base = time.strftime("%Y%m%d-%H%M%S")
        sid, n = base, 1
        while (self.dir / f"{sid}.jsonl").exists():  # two sessions started in the same second
            n += 1
            sid = f"{base}-{n}"
        return sid

    def _file(self, session_id: str) -> Path:
        if not _ID.match(session_id):
            raise ValueError(f"not a session id: {session_id!r}")  # also keeps `../x` out of the path
        return self.dir / f"{session_id}.jsonl"

    def append(self, session_id: str, entry: dict[str, Any]) -> None:
        path = self._file(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def load(self, session_id: str) -> list[dict[str, Any]]:
        path = self._file(session_id)
        if not path.exists():
            raise FileNotFoundError(f"no saved session {session_id} in {self.dir}")
        out: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("role") in ("user", "assistant", "tool"):
                out.append(entry)
        return out

    def list(self) -> list[SessionInfo]:
        """Saved sessions, newest first."""
        infos: list[SessionInfo] = []
        for path in self.dir.glob("*.jsonl") if self.dir.exists() else []:
            if not _ID.match(path.stem):
                continue
            entries = self.load(path.stem)
            users = [e for e in entries if e["role"] == "user"]
            first = users[0]["text"].replace("\n", " ")[:80] if users else ""
            infos.append(SessionInfo(path.stem, path, len(users), first, path.stat().st_mtime))
        return sorted(infos, key=lambda i: (i.modified, i.id), reverse=True)

    def latest(self) -> str | None:
        found = self.list()
        return found[0].id if found else None
