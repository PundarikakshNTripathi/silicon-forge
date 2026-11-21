# SPDX-License-Identifier: AGPL-3.0-or-later
"""SiliconForge HTTP API (FastAPI): runs, live event streaming, artifacts, providers, local models, skills, system."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from services.orchestrator.api import routes_workspace, system
from services.orchestrator.api.agent_manager import AgentManager
from services.orchestrator.api.manager import RunManager, RunRequest
from services.orchestrator.api.store import TERMINAL, Store
from services.orchestrator.config import load_flow_config
from services.orchestrator.inference.catalog import load_catalog
from services.orchestrator.inference.estimate import estimate_profile_cost
from services.orchestrator.inference.gpu import GpuBusyError, foreign_servers
from services.orchestrator.inference.llamacpp import LlamaCpp, LlamaServer, ServerConfig, gpu_info
from services.orchestrator.inference.secrets import delete_secret, set_secret
from services.orchestrator.paths import RESOURCE_ROOT, data_root
from services.orchestrator.skills.registry import SkillRegistry
from services.orchestrator.version import __version__

REPO_ROOT = RESOURCE_ROOT
DEFAULT_SETTINGS: dict[str, Any] = {
    "default_profile": "replay",
    "default_backend": "simulated",
    "timing_corner": "worst",
    "cli_bridge_enabled": False,
    "llama_ctx": 16384,
    "llama_kv": "q8_0",
}
ARTIFACT_EXTS = {".sv", ".v", ".sby", ".vcd", ".gds", ".json", ".log", ".png", ".txt", ".yaml", ".md", ".ys"}


class SecretBody(BaseModel):
    value: str


class ServerBody(BaseModel):
    model: str = ""  # a file in SiliconForge's model folder, or...
    path: str | None = None  # ...any GGUF on disk (e.g. the Hugging Face cache), used in place
    cpu: bool = False
    ctx: int | None = None
    kv: str | None = None
    port: int = 8189


class PullBody(BaseModel):
    repo: str
    filename: str


class SkillBody(BaseModel):
    content: str


class RetrieveBody(BaseModel):
    query: str
    top_k: int = 2


class LocalModels:
    """In-process control of the llama.cpp server and model downloads."""

    def __init__(self) -> None:
        self.lc = LlamaCpp(home=data_root() / "models")
        self.server: LlamaServer | None = None
        self.pulls: dict[str, dict[str, Any]] = {}

    def status(self) -> dict[str, Any]:
        return {
            "installed": self.lc.server_exe is not None, "tag": self.lc.installed_tag(),
            "models": [{"name": p.name, "size_gb": round(p.stat().st_size / 1e9, 2)} for p in self.lc.models()],
            "server": {"running": bool(self.server and self.server.is_healthy()),
                       "model": self.server.cfg.model_path.name if self.server else None,
                       "props": self.server.props() if self.server and self.server.is_healthy() else {}},
            "gpu": gpu_info(),
            "discovered": self.lc.discover_models(),
            "other_servers": [x for x in foreign_servers(own_port=8189) if not x["ours"]],
            "pulls": self.pulls,
        }

    def start(self, body: ServerBody, defaults: dict[str, Any]) -> None:
        exe = self.lc.server_exe
        if exe is None:
            raise HTTPException(400, "llama-server is not installed")
        path = Path(body.path) if body.path else self.lc.home / "gguf" / body.model
        if not path.is_file() or path.suffix != ".gguf":
            raise HTTPException(404, f"model '{body.path or body.model}' not found")
        if self.server:
            self.server.stop()
        cfg = ServerConfig(model_path=path, ctx=body.ctx or defaults["llama_ctx"], kv_type=body.kv or defaults["llama_kv"],
                           port=body.port, gpu_layers=0 if body.cpu else 99)
        self.server = LlamaServer(exe, cfg)
        try:
            self.server.start()
        except GpuBusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (RuntimeError, TimeoutError) as exc:
            raise HTTPException(500, str(exc)) from exc

    def stop(self) -> None:
        if self.server:
            self.server.stop()

    def pull(self, body: PullBody) -> str:
        pid = uuid.uuid4().hex[:8]
        self.pulls[pid] = {"repo": body.repo, "file": body.filename, "status": "downloading"}

        def work() -> None:
            try:
                self.lc.pull_model(body.repo, body.filename)
                self.pulls[pid]["status"] = "done"
            except Exception as exc:  # noqa: BLE001
                self.pulls[pid].update(status="failed", error=str(exc))

        threading.Thread(target=work, daemon=True).start()
        return pid

    def install(self) -> str:
        pid = uuid.uuid4().hex[:8]
        self.pulls[pid] = {"repo": "ggml-org/llama.cpp", "file": "llama-server", "status": "downloading"}

        def work() -> None:
            try:
                self.lc.install()
                self.pulls[pid]["status"] = "done"
            except Exception as exc:  # noqa: BLE001
                self.pulls[pid].update(status="failed", error=str(exc))

        threading.Thread(target=work, daemon=True).start()
        return pid


def create_app(db_path: str | Path | None = None, serve_ui: bool = True, runs_root: Path | None = None,
               agents: AgentManager | None = None) -> FastAPI:
    app = FastAPI(title="SiliconForge API", version=__version__)
    runs_root = Path(runs_root) if runs_root else data_root() / "runs"
    store = Store(db_path or runs_root / "siliconforge.db")
    manager = RunManager(store, REPO_ROOT, runs_root=runs_root)
    local = LocalModels()
    agents = agents or AgentManager()
    token = os.getenv("SILICONFORGE_API_TOKEN", "")

    app.add_middleware(
        CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"], allow_headers=["*"],
    )

    def auth(request: Request, token_q: str | None = Query(default=None, alias="token")) -> None:
        if not token:
            return
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or token_q
        if supplied != token:
            raise HTTPException(401, "invalid or missing API token")

    api = Depends(auth)

    def settings() -> dict[str, Any]:
        return {**DEFAULT_SETTINGS, **store.get_settings()}

    # --- health / system -----------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "siliconforge", "version": app.version}

    @app.get("/api/system/doctor", dependencies=[api])
    def doctor(probe: bool = True) -> dict[str, Any]:
        return system.doctor(probe_backends=probe)

    # --- examples ------------------------------------------------------------------------------------

    @app.get("/api/examples", dependencies=[api])
    def examples() -> list[dict[str, Any]]:
        out = []
        for fx in sorted((REPO_ROOT / "hardware" / "examples").glob("*/fixtures/contract.json")):
            ex = fx.parent.parent
            out.append({"name": ex.name, "spec": (ex / "spec.md").read_text(encoding="utf-8"),
                        "replay_supported": True})
        return out

    # --- runs ----------------------------------------------------------------------------------------

    @app.post("/api/runs", dependencies=[api], status_code=202)
    def start_run(req: RunRequest) -> dict[str, Any]:
        try:
            run_id = manager.start(req)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"run_id": run_id}

    @app.get("/api/runs", dependencies=[api])
    def list_runs(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return store.list_runs(limit, offset)

    @app.get("/api/runs/{run_id}", dependencies=[api])
    def get_run(run_id: str) -> dict[str, Any]:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run

    @app.delete("/api/runs/{run_id}", dependencies=[api])
    def delete_run(run_id: str) -> dict[str, bool]:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        if run["status"] in ("queued", "running"):
            raise HTTPException(409, "cancel the run before deleting it")
        store.delete_run(run_id)
        return {"deleted": True}

    @app.post("/api/runs/{run_id}/cancel", dependencies=[api])
    def cancel(run_id: str) -> dict[str, bool]:
        return {"cancelling": manager.cancel(run_id)}

    @app.get("/api/runs/{run_id}/events", dependencies=[api])
    async def events(run_id: str, request: Request, after: int = -1) -> StreamingResponse:
        if store.get_run(run_id) is None:
            raise HTTPException(404, "run not found")
        last_id = request.headers.get("last-event-id")
        start = int(last_id) if last_id and last_id.lstrip("-").isdigit() else after

        async def gen():
            last = start
            idle = 0
            while True:
                batch = store.events_after(run_id, last)
                for e in batch:
                    last = e["seq"]
                    yield f"id: {e['seq']}\ndata: {json.dumps(e, default=str)}\n\n"
                run = store.get_run(run_id)
                if run and run["status"] in TERMINAL and not batch:
                    yield f"event: done\ndata: {json.dumps({'status': run['status']})}\n\n"
                    return
                if await request.is_disconnected():
                    return
                idle = 0 if batch else idle + 1
                if idle and idle % 50 == 0:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.3)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/events/all", dependencies=[api])
    def all_events(run_id: str) -> list[dict[str, Any]]:
        return store.events_after(run_id, -1, limit=100_000)

    def run_dir(run_id: str) -> Path:
        d = (runs_root / run_id).resolve()
        if not str(d).startswith(str(runs_root.resolve())) or not d.exists():
            raise HTTPException(404, "run directory not found")
        return d

    @app.get("/api/runs/{run_id}/artifacts", dependencies=[api])
    def artifacts(run_id: str) -> list[dict[str, Any]]:
        base = run_dir(run_id)
        out = []
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix.lower() in ARTIFACT_EXTS and p.stat().st_size < 200_000_000:
                rel = p.relative_to(base).as_posix()
                if "/runs/" in f"/{rel}" and "/final/" not in rel and not rel.endswith((".log", ".json")):
                    continue  # skip LibreLane internals; keep final/ outputs
                out.append({"path": rel, "size": p.stat().st_size, "kind": p.suffix.lower().lstrip(".")})
        return out[:500]

    @app.get("/api/runs/{run_id}/artifacts/{path:path}", dependencies=[api])
    def artifact(run_id: str, path: str) -> FileResponse:
        base = run_dir(run_id)
        target = (base / path).resolve()
        if not str(target).startswith(str(base)) or not target.is_file():
            raise HTTPException(404, "artifact not found")
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=mime, filename=target.name if target.suffix in (".gds", ".sv", ".v") else None)

    # --- estimate / cost -----------------------------------------------------------------------------

    @app.get("/api/estimate", dependencies=[api])
    def estimate(profile: str = "replay") -> dict[str, Any]:
        try:
            return estimate_profile_cost(load_catalog(), profile)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/cost/summary", dependencies=[api])
    def cost_summary() -> dict[str, Any]:
        return store.cost_rollup()

    routes_workspace.register(app, api, agents)

    # --- providers / secrets -------------------------------------------------------------------------

    @app.get("/api/providers", dependencies=[api])
    def providers() -> dict[str, Any]:
        return system.provider_status(cli_bridge_enabled=bool(settings()["cli_bridge_enabled"]))

    @app.put("/api/secrets/{env_name}", dependencies=[api])
    def put_secret(env_name: str, body: SecretBody) -> dict[str, bool]:
        if not env_name.replace("_", "").isalnum() or not body.value.strip():
            raise HTTPException(400, "invalid secret")
        return {"stored": set_secret(env_name, body.value.strip())}

    @app.delete("/api/secrets/{env_name}", dependencies=[api])
    def remove_secret(env_name: str) -> dict[str, bool]:
        return {"deleted": delete_secret(env_name)}

    # --- settings ------------------------------------------------------------------------------------

    @app.get("/api/settings", dependencies=[api])
    def get_settings() -> dict[str, Any]:
        return settings()

    @app.put("/api/settings", dependencies=[api])
    def put_settings(values: dict[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(DEFAULT_SETTINGS)
        if unknown:
            raise HTTPException(400, f"unknown settings: {sorted(unknown)}")
        store.put_settings(values)
        return settings()

    # --- local models (llama.cpp) --------------------------------------------------------------------

    @app.get("/api/models", dependencies=[api])
    def models() -> dict[str, Any]:
        return local.status()

    @app.post("/api/models/install", dependencies=[api], status_code=202)
    def install_llama() -> dict[str, str]:
        return {"job": local.install()}

    @app.post("/api/models/pull", dependencies=[api], status_code=202)
    def pull_model(body: PullBody) -> dict[str, str]:
        return {"job": local.pull(body)}

    @app.post("/api/models/server/start", dependencies=[api])
    def start_server(body: ServerBody) -> dict[str, Any]:
        local.start(body, settings())
        return local.status()

    @app.post("/api/models/server/stop", dependencies=[api])
    def stop_server() -> dict[str, Any]:
        local.stop()
        return local.status()

    # --- skills (knowledge packs) --------------------------------------------------------------------

    packs_dir = REPO_ROOT / "services" / "orchestrator" / "skills" / "packs"

    @app.get("/api/skills", dependencies=[api])
    def list_skills() -> list[dict[str, Any]]:
        return [{"name": p.name, "content": p.content, "tokens": len(p.tokens)} for p in SkillRegistry().packs]

    @app.put("/api/skills/{name}", dependencies=[api])
    def put_skill(name: str, body: SkillBody) -> dict[str, bool]:
        if not name.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(400, "invalid skill name")
        (packs_dir / f"{name}.md").write_text(body.content, encoding="utf-8")
        return {"saved": True}

    @app.post("/api/skills/retrieve", dependencies=[api])
    def retrieve(body: RetrieveBody) -> list[dict[str, Any]]:
        return [{"name": p.name, "content": p.content} for p in SkillRegistry().retrieve(body.query, body.top_k)]

    @app.get("/api/benchmarks", dependencies=[api])
    def benchmarks() -> list[dict[str, Any]]:
        out = []
        folders = [REPO_ROOT / "benchmarks" / "results", data_root() / "benchmarks" / "results"]  # committed, then yours
        seen: set[str] = set()
        for f in sorted((f for d in folders if d.is_dir() for f in d.glob("*.json")), key=lambda p: p.name, reverse=True):
            if f.name in seen:
                continue
            seen.add(f.name)
            try:
                out.append({"file": f.name, "data": json.loads(f.read_text(encoding="utf-8"))})
            except (OSError, ValueError):
                continue
        return out

    @app.get("/api/config/defaults", dependencies=[api])
    def config_defaults() -> dict[str, Any]:
        cfg = load_flow_config()
        return {"pdk": cfg.pdk, "targets": cfg.targets, "pipeline": cfg.pipeline,
                "rl": {k: v for k, v in cfg.rl_optimizer.items() if k != "reward_weights"}}

    # --- static UI -----------------------------------------------------------------------------------

    dist = REPO_ROOT / "services" / "dashboard" / "dist"
    if serve_ui and dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            """Serve built files, falling back to index.html so client-side routes survive a refresh."""
            if path.startswith("api/"):
                raise HTTPException(404, "not found")
            target = (dist / path).resolve()
            if path and str(target).startswith(str(dist.resolve())) and target.is_file():
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


app_factory = create_app
