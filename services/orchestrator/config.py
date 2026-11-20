"""Typed configuration loader for SiliconForge."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List
import yaml


@dataclass
class TierConfig:
    model: str
    input_cost_per_1m: float
    output_cost_per_1m: float


@dataclass
class InferenceConfig:
    provider: str = "mock"
    enable_semantic_cache: bool = True
    max_budget_usd: float = 15.0
    max_total_tokens: int = 500_000
    tiers: Dict[str, TierConfig] = field(default_factory=dict)


@dataclass
class PdkConfig:
    pdk_name: str
    node_nm: int
    stdcell_library: str
    nominal_voltage_v: float
    min_clock_period_ns: float
    allowed_cell_prefixes: List[str] = field(default_factory=list)
    allowed_flow_vars: List[str] = field(default_factory=list)


@dataclass
class FlowConfig:
    project_name: str
    eda_mode: str
    pdk: str
    workspace_root: Path
    telemetry_binary: Path
    pipeline: Dict[str, Any]
    targets: Dict[str, float]
    rl_optimizer: Dict[str, Any]
    inference: InferenceConfig
    pdk_spec: PdkConfig


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

    tiers = {
        name: TierConfig(
            model=vals["model"],
            input_cost_per_1m=float(vals["input_cost_per_1m"]),
            output_cost_per_1m=float(vals["output_cost_per_1m"]),
        )
        for name, vals in raw.get("inference", {}).get("tiers", {}).items()
    }

    inf_raw = raw.get("inference", {})
    inf_cfg = InferenceConfig(
        provider=inf_raw.get("provider", "mock"),
        enable_semantic_cache=bool(inf_raw.get("enable_semantic_cache", True)),
        max_budget_usd=float(inf_raw.get("max_budget_usd", 15.0)),
        max_total_tokens=int(inf_raw.get("max_total_tokens", 500_000)),
        tiers=tiers,
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
        telemetry_binary=root / raw.get("telemetry_binary", "services/telemetry_engine/build/sf-telemetry"),
        pipeline=dict(raw.get("pipeline", {})),
        targets={k: float(v) for k, v in raw.get("targets", {}).items()},
        rl_optimizer=dict(raw.get("rl_optimizer", {})),
        inference=inf_cfg,
        pdk_spec=pdk_cfg,
    )
