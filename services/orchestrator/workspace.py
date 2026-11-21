"""Workspaces: the directory the agent works in, plus the rules it must follow there.

A workspace is any folder. If it holds a `siliconforge.yaml` that file sets the profile, backend, PDK and a model
policy; otherwise sensible defaults apply and nothing is written until you ask (`siliconforge init`). Runs, caches and
the agent's checkpoints live under `<workspace>/.siliconforge/`, so a project is self-contained and can be committed
or deleted as a unit.

Instruction files (`SILICONFORGE.md`, falling back to `AGENTS.md`) are read into the agent's prompt, the same convention
Claude Code, Codex and Antigravity use, so a repo that already has one keeps working.

The model policy is enforced by the router, not requested politely: with `allowed_models: ["gemini-3.8-flash-*"]` any
other model in the profile chain is dropped, and a chain that ends up empty is an error.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from services.orchestrator.paths import data_root

MANIFEST = "siliconforge.yaml"
INSTRUCTION_FILES = ("SILICONFORGE.md", "AGENTS.md")
STATE_DIR = ".siliconforge"
MAX_INSTRUCTION_CHARS = 24_000
RECENTS_LIMIT = 12


class WorkspaceError(ValueError):
    pass


@dataclass
class Workspace:
    root: Path
    name: str = ""
    profile: str = "replay"
    backend: str = "simulated"
    pdk: str | None = None
    allowed_models: list[str] = field(default_factory=list)
    max_budget_usd: float | None = None
    max_total_tokens: int | None = None
    has_manifest: bool = False

    # ------------------------------------------------------------------ paths
    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "ai-usage.jsonl"

    def resolve(self, relative: str | Path) -> Path:
        """Resolve a path inside the workspace; anything that escapes it (`..`, absolute, symlink) is refused."""
        p = (self.root / relative).resolve()
        root = self.root.resolve()
        if p != root and root not in p.parents:
            raise WorkspaceError(f"'{relative}' is outside the workspace {root}")
        return p

    # ------------------------------------------------------------ instructions
    def instructions(self) -> tuple[str, str | None]:
        """(text, source filename). Empty text when the workspace has no instruction file."""
        for fname in INSTRUCTION_FILES:
            f = self.root / fname
            if f.is_file():
                text = f.read_text(encoding="utf-8", errors="replace")
                if len(text) > MAX_INSTRUCTION_CHARS:
                    text = text[:MAX_INSTRUCTION_CHARS] + "\n\n[truncated]"
                return text, fname
        return "", None

    # ------------------------------------------------------------------ config
    def apply(self, cfg: Any) -> Any:
        """Point a FlowConfig at this workspace and impose its policy. Returns the same object."""
        cfg.workspace_root = self.runs_dir
        cfg.eda_mode = self.backend or cfg.eda_mode
        if self.profile:
            cfg.inference.profile = self.profile
        if self.pdk:
            cfg.pdk = self.pdk
        if self.allowed_models:
            cfg.inference.allowed_models = list(self.allowed_models)
        if self.max_budget_usd is not None:
            cfg.inference.max_budget_usd = self.max_budget_usd
        if self.max_total_tokens is not None:
            cfg.inference.max_total_tokens = self.max_total_tokens
        if cfg.inference.cache_path:
            cfg.inference.cache_path = str(self.state_dir / "llm-cache.sqlite")
        cfg.inference.ledger_path = str(self.ledger_path)
        return cfg

    def describe(self) -> dict[str, Any]:
        text, src = self.instructions()
        return {
            "root": str(self.root), "name": self.name or self.root.name, "profile": self.profile,
            "backend": self.backend, "pdk": self.pdk, "allowed_models": self.allowed_models,
            "max_budget_usd": self.max_budget_usd, "max_total_tokens": self.max_total_tokens, "has_manifest": self.has_manifest,
            "instructions_file": src, "instructions_chars": len(text),
        }


def load_workspace(path: str | Path) -> Workspace:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise WorkspaceError(f"'{root}' is not a directory")
    manifest = root / MANIFEST
    if not manifest.is_file():
        return Workspace(root=root, name=root.name)
    try:
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise WorkspaceError(f"{manifest}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkspaceError(f"{manifest}: expected a mapping at the top level")
    policy = raw.get("policy") or {}
    allowed = policy.get("allowed_models") or []
    if isinstance(allowed, str):
        allowed = [allowed]
    budget = policy.get("max_budget_usd")
    return Workspace(
        root=root,
        name=str(raw.get("name") or root.name),
        profile=str(raw.get("profile", "replay")),
        backend=str(raw.get("backend", "simulated")),
        pdk=str(raw["pdk"]) if raw.get("pdk") else None,
        allowed_models=[str(a) for a in allowed],
        max_budget_usd=float(budget) if budget is not None else None,
        max_total_tokens=int(policy["max_total_tokens"]) if policy.get("max_total_tokens") is not None else None,
        has_manifest=True,
    )


def find_workspace(start: str | Path | None = None) -> Workspace:
    """Nearest ancestor holding a manifest, else `start` itself (the Claude Code behavior: run it where you are)."""
    here = Path(start or os.getcwd()).expanduser().resolve()
    for d in (here, *here.parents):
        if (d / MANIFEST).is_file():
            return load_workspace(d)
    return load_workspace(here)


def init_workspace(path: str | Path, *, name: str | None = None, profile: str = "dev", backend: str = "wsl",
                   pdk: str | None = None, allowed_models: list[str] | None = None,
                   max_budget_usd: float | None = None, overwrite: bool = False) -> Workspace:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / MANIFEST
    if manifest.exists() and not overwrite:
        raise WorkspaceError(f"{manifest} already exists (pass overwrite to replace it)")
    doc: dict[str, Any] = {"name": name or root.name, "profile": profile, "backend": backend}
    if pdk:
        doc["pdk"] = pdk
    policy: dict[str, Any] = {}
    if allowed_models:
        policy["allowed_models"] = allowed_models
    if max_budget_usd is not None:
        policy["max_budget_usd"] = max_budget_usd
    if policy:
        doc["policy"] = policy
    manifest.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    gi = root / ".gitignore"
    line = f"{STATE_DIR}/"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if line not in existing.splitlines():
        gi.write_text(existing + ("" if existing.endswith("\n") or not existing else "\n") + line + "\n", encoding="utf-8")
    return load_workspace(root)


# ------------------------------------------------------------------------------------------------ recents

def _recents_file() -> Path:
    return data_root() / "workspaces.json"


def recent_workspaces() -> list[dict[str, Any]]:
    try:
        items = json.loads(_recents_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [i for i in items if isinstance(i, dict) and Path(str(i.get("path", ""))).is_dir()]


def remember_workspace(ws: Workspace) -> None:
    items = [i for i in recent_workspaces() if i.get("path") != str(ws.root)]
    items.insert(0, {"path": str(ws.root), "name": ws.name or ws.root.name, "profile": ws.profile})
    try:
        _recents_file().write_text(json.dumps(items[:RECENTS_LIMIT], indent=2), encoding="utf-8")
    except OSError:
        pass  # recents are a convenience; never fail a run over them
