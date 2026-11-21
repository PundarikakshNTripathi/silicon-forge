"""HTTP routes for workspaces, a read-only folder browser and agent sessions.

The API only listens on localhost behind a per-launch token, so the folder browser (which a native file dialog also
is) lists directories anywhere the user's account can read. It returns directories only, never file contents; reading a
file is limited to inside an opened workspace and goes through the same confinement the agent uses.
"""
from __future__ import annotations

import asyncio
import json
import os
import string
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.orchestrator.api.agent_manager import AgentManager, SessionBusyError
from services.orchestrator.workspace import (
    MANIFEST,
    WorkspaceError,
    init_workspace,
    load_workspace,
    recent_workspaces,
    remember_workspace,
)

MAX_FILE_BYTES = 400_000


class OpenWorkspace(BaseModel):
    path: str
    create: bool = False
    profile: str = "dev"
    backend: str = "wsl"
    pdk: str | None = None
    allowed_models: list[str] = Field(default_factory=list)


class NewSession(BaseModel):
    workspace: str
    profile: str | None = None
    mode: str = "ask"


class Message(BaseModel):
    text: str = Field(min_length=1)


class Decision(BaseModel):
    allow: bool


class ModeBody(BaseModel):
    mode: str


def _roots() -> list[dict[str, str]]:
    if os.name == "nt":
        return [{"name": f"{d}:", "path": f"{d}:\\"} for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    return [{"name": "/", "path": "/"}, {"name": "Home", "path": str(Path.home())}]


def register(app: FastAPI, api: Any, agents: AgentManager) -> None:
    # --- workspaces ----------------------------------------------------------------------------------

    @app.get("/api/workspaces", dependencies=[api])
    def workspaces() -> dict[str, Any]:
        return {"recent": recent_workspaces(), "home": str(Path.home())}

    @app.post("/api/workspaces/open", dependencies=[api])
    def open_workspace(body: OpenWorkspace) -> dict[str, Any]:
        try:
            root = Path(body.path).expanduser()
            if body.create:
                ws = init_workspace(root, profile=body.profile, backend=body.backend, pdk=body.pdk,
                                    allowed_models=body.allowed_models or None) if not (root / MANIFEST).exists() \
                    else load_workspace(root)
            else:
                ws = load_workspace(root)
        except WorkspaceError as exc:
            raise HTTPException(400, str(exc)) from exc
        remember_workspace(ws)
        return ws.describe()

    @app.get("/api/fs", dependencies=[api])
    def browse(path: str = "") -> dict[str, Any]:
        if not path:
            return {"path": "", "parent": None, "roots": _roots(), "entries": []}
        p = Path(path).expanduser()
        if not p.is_dir():
            raise HTTPException(404, "not a directory")
        entries = []
        try:
            for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
                try:
                    if child.is_dir() and not child.name.startswith(("$", ".git")):
                        entries.append({"name": child.name, "path": str(child),
                                        "workspace": (child / MANIFEST).is_file()})
                except OSError:
                    continue
        except PermissionError as exc:
            raise HTTPException(403, "permission denied") from exc
        parent = str(p.parent) if p.parent != p else None
        return {"path": str(p), "parent": parent, "roots": _roots(), "entries": entries[:500],
                "workspace": (p / MANIFEST).is_file()}

    @app.get("/api/workspace/tree", dependencies=[api])
    def tree(root: str, path: str = ".") -> dict[str, Any]:
        from services.orchestrator.agent.tools import ToolBox, ToolError

        try:
            return ToolBox(load_workspace(root)).list_dir(path)
        except (WorkspaceError, ToolError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/workspace/file", dependencies=[api])
    def read_file(root: str, path: str) -> dict[str, Any]:
        try:
            p = load_workspace(root).resolve(path)
        except WorkspaceError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not p.is_file():
            raise HTTPException(404, "not a file")
        if p.stat().st_size > MAX_FILE_BYTES:
            raise HTTPException(413, "file too large to preview")
        return {"path": path, "content": p.read_text(encoding="utf-8", errors="replace")}

    # --- agent sessions ------------------------------------------------------------------------------

    @app.post("/api/agent/sessions", dependencies=[api], status_code=201)
    def new_session(body: NewSession) -> dict[str, Any]:
        try:
            m = agents.create(body.workspace, body.profile, body.mode)
        except (WorkspaceError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": m.id, "workspace": m.workspace, "mode": m.session.mode}

    @app.post("/api/agent/sessions/{sid}/messages", dependencies=[api], status_code=202)
    def send(sid: str, body: Message) -> dict[str, bool]:
        try:
            agents.send(sid, body.text)
        except KeyError as exc:
            raise HTTPException(404, "session not found") from exc
        except SessionBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"accepted": True}

    @app.get("/api/agent/sessions/{sid}/events", dependencies=[api])
    def poll(sid: str, after: int = -1) -> dict[str, Any]:
        try:
            m = agents.get(sid)
        except KeyError as exc:
            raise HTTPException(404, "session not found") from exc
        return {"events": m.since(after), "busy": m.busy, "mode": m.session.mode}

    @app.get("/api/agent/sessions/{sid}/stream", dependencies=[api])
    async def stream(sid: str, request: Request, after: int = -1) -> StreamingResponse:
        try:
            m = agents.get(sid)
        except KeyError as exc:
            raise HTTPException(404, "session not found") from exc

        async def gen():
            last = after
            while True:
                for e in m.since(last):
                    last = e["seq"]
                    yield f"id: {e['seq']}\ndata: {json.dumps(e, default=str)}\n\n"
                if await request.is_disconnected() or sid not in agents.sessions:
                    return
                await asyncio.sleep(0.25)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/agent/sessions/{sid}/approvals/{aid}", dependencies=[api])
    def decide(sid: str, aid: str, body: Decision) -> dict[str, bool]:
        try:
            return {"answered": agents.answer(sid, aid, body.allow)}
        except KeyError as exc:
            raise HTTPException(404, "session not found") from exc

    @app.put("/api/agent/sessions/{sid}/mode", dependencies=[api])
    def set_mode(sid: str, body: ModeBody) -> dict[str, str]:
        try:
            agents.set_mode(sid, body.mode)
        except KeyError as exc:
            raise HTTPException(404, "session not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"mode": body.mode}

    @app.post("/api/agent/sessions/{sid}/undo", dependencies=[api])
    def undo(sid: str) -> dict[str, str]:
        try:
            return {"result": agents.undo(sid)}
        except KeyError as exc:
            raise HTTPException(404, "session not found") from exc

    @app.delete("/api/agent/sessions/{sid}", dependencies=[api])
    def close(sid: str) -> dict[str, bool]:
        agents.close(sid)
        return {"closed": True}
