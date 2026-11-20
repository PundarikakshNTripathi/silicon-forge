"""Typed configuration loader for SiliconForge."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class InferenceConfig:
    profile: str = "replay"  # routing profile from configs/models.yaml
    catalog_path: str = "configs/models.yaml"
    enable_semantic_cache: bool = True  # response cache (exact match); name kept for config compatibility
    cache_path: str | None = None  # SQLite file for a persistent cache; None = in-memory
    max_budget_usd: float = 15.0
    max_total_tokens: int = 500_000
    stage_token_budgets: dict[str, int] = field(default_factory=dict)
    cli_bridge_enabled: bool = False  # opt-in subscription CLI bridge (see docs/providers.md)

    @property
    def provider(self) -> str:
        """Legacy alias."""
        return self.profile


@dataclass
class PdkConfig:
    pdk_name: str
    node_nm: int
    stdcell_library: str
    nominal_voltage_v: float
    min_clock_period_ns: float
    allowed_cell_prefixes: list[str] = field(default_factory=list)
    allowed_flow_vars: list[str] = field(default_factory=list)


@dataclass
class FlowConfig:
    project_name: str
    eda_mode: str
    pdk: str
    workspace_root: Path
    telemetry_binary: Path
    pipeline: dict[str, Any]
    targets: dict[str, float]
    rl_optimizer: dict[str, Any]
    inference: InferenceConfig
    pdk_spec: PdkConfig
    eda: dict[str, Any] = field(default_factory=dict)


def _resolve_binary(path: Path) -> Path:
    """Return `path`, or `path.exe` on Windows when only the .exe exists."""
    if path.exists():
        return path
    exe = path.with_name(path.name + ".exe")
    return exe if exe.exists() else path


def load_flow_config(
    config_path: str | Path = "configs/flow_default.yaml",
    repo_root: str | Path | None = None,
) -> FlowConfig:
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    cfg_file = Path(config_path)
    if not cfg_file.is_absolute():
        cfg_file = root / cfg_file
    raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))

    pdk_name = raw.get("pdk", "sky130A")
    pdk_file = root / "configs" / "pdks" / f"{pdk_name}.yaml"
    pdk_raw = yaml.safe_load(pdk_file.read_text(encoding="utf-8"))

    inf_raw = raw.get("inference", {})
    legacy = {"mock": "replay", "replay": "replay"}
    profile = inf_raw.get("profile") or legacy.get(inf_raw.get("provider", "replay"), inf_raw.get("provider", "replay"))
    cache_path = inf_raw.get("cache_path")
    inf_cfg = InferenceConfig(
        profile=str(profile),
        catalog_path=str(inf_raw.get("catalog_path", "configs/models.yaml")),
        enable_semantic_cache=bool(inf_raw.get("enable_semantic_cache", True)),
        cache_path=str(root / cache_path) if cache_path else None,
        max_budget_usd=float(inf_raw.get("max_budget_usd", 15.0)),
        max_total_tokens=int(inf_raw.get("max_total_tokens", 500_000)),
        stage_token_budgets={k: int(v) for k, v in (inf_raw.get("stage_token_budgets") or {}).items()},
        cli_bridge_enabled=bool(inf_raw.get("cli_bridge_enabled", False)),
    )

    pdk_cfg = PdkConfig(
        pdk_name=pdk_raw["pdk_name"],
        node_nm=int(pdk_raw["node_nm"]),
        stdcell_library=pdk_raw["stdcell_library"],
        nominal_voltage_v=float(pdk_raw["nominal_voltage_v"]),
        min_clock_period_ns=float(pdk_raw["min_clock_period_ns"]),
        allowed_cell_prefixes=list(pdk_raw.get("allowed_cell_prefixes", [])),
        allowed_flow_vars=list(pdk_raw.get("allowed_flow_vars", [])),
    )

    return FlowConfig(
        project_name=raw.get("project_name", "siliconforge"),
        eda_mode=raw.get("eda_mode", "mock"),
        pdk=pdk_name,
        workspace_root=root / raw.get("workspace_root", "runs"),
        telemetry_binary=_resolve_binary(
            root / raw.get("telemetry_binary", "services/telemetry_engine/build/sf-telemetry")
        ),
        pipeline=dict(raw.get("pipeline", {})),
        targets={k: float(v) for k, v in raw.get("targets", {}).items()},
        rl_optimizer=dict(raw.get("rl_optimizer", {})),
        inference=inf_cfg,
        pdk_spec=pdk_cfg,
        eda=dict(raw.get("eda", {})),
    )
