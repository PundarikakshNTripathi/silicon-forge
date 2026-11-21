# SPDX-License-Identifier: AGPL-3.0-or-later
"""System introspection shared by the CLI `doctor` and the API: toolchain, GPU, providers, local model server."""
from __future__ import annotations

import platform
import shutil
from pathlib import Path
from typing import Any

from services.orchestrator.config import load_flow_config
from services.orchestrator.eda.bridge import MODES, EDABridge
from services.orchestrator.inference.catalog import load_catalog
from services.orchestrator.inference.llamacpp import LlamaCpp, gpu_info
from services.orchestrator.inference.providers.cli_bridge import DEFAULT_COMMANDS, TOS_WARNING
from services.orchestrator.inference.secrets import has_secret


def doctor(config_path: str = "configs/flow_default.yaml", probe_backends: bool = True) -> dict[str, Any]:
    cfg = load_flow_config(config_path)
    host = {
        t: shutil.which(t) is not None
        for t in ("python", "node", "pnpm", "cmake", "git", "wsl", "docker", "nvidia-smi", "llama-server")
    }
    backends: dict[str, Any] = {"simulated": {"available": True, "tools": {}, "note": "surrogate models; results labelled simulated"}}
    if probe_backends:
        for mode in MODES:
            if mode == "simulated":
                continue
            cfg.eda_mode = mode
            try:
                tools = EDABridge(cfg).check_tools()
                backends[mode] = {"available": bool(tools.get("sby") and tools.get("yosys")), "tools": tools}
            except Exception as exc:  # noqa: BLE001 - diagnostics must never crash
                backends[mode] = {"available": False, "tools": {}, "error": f"{type(exc).__name__}: {exc}"}
    exe = "sf-telemetry.exe" if platform.system() == "Windows" else "sf-telemetry"
    llama = LlamaCpp()
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "host_tools": host,
        "gpu": gpu_info(),
        "backends": backends,
        "telemetry_engine_built": (Path(cfg.telemetry_binary).exists()),
        "telemetry_binary": exe,
        "llama_cpp": {"installed": llama.server_exe is not None, "tag": llama.installed_tag(),
                      "models": [p.name for p in llama.models()]},
    }


def provider_status(catalog_path: str = "configs/models.yaml", cli_bridge_enabled: bool = False) -> dict[str, Any]:
    import httpx

    cat = load_catalog(catalog_path)
    providers = []
    for name, cfg in cat.providers.items():
        kind = cfg.get("kind")
        item: dict[str, Any] = {"name": name, "kind": kind, "local": bool(cfg.get("local")) or kind == "replay"}
        if kind in ("openai_compat", "anthropic"):
            env = cfg.get("api_key_env")
            item["key_env"] = env
            item["key_present"] = has_secret(env) if env else None
        if kind == "openai_compat" and cfg.get("local"):
            base = str(cfg.get("base_url", "")).removesuffix("/v1")
            try:
                item["reachable"] = httpx.get(f"{base}/health", timeout=1.5).status_code == 200
            except httpx.HTTPError:
                item["reachable"] = False
        if kind == "cli_bridge":
            cli = cfg.get("cli", "claude")
            item["installed"] = shutil.which(DEFAULT_COMMANDS.get(cli, [cli])[0]) is not None
            item["enabled"] = cli_bridge_enabled
            item["warning"] = TOS_WARNING
        providers.append(item)
    profiles = []
    for pname, tiers in cat.profiles.items():
        used = {t.provider for chain in tiers.values() for t in chain}
        profiles.append({
            "name": pname,
            "tiers": {tier: [{"provider": t.provider, "model": t.model} for t in chain] for tier, chain in tiers.items()},
            "providers": sorted(used),
        })
    return {"providers": providers, "profiles": profiles, "pricing_as_of": cat.pricing_as_of,
            "models": {m: p.__dict__ for m, p in cat.models.items()}}
