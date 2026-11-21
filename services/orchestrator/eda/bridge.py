# SPDX-License-Identifier: AGPL-3.0-or-later
"""EDA bridge: selects an execution backend by `eda_mode` and exposes one stable interface to the pipeline.

Modes: "simulated" (surrogate models), "native" (tools on PATH), "wsl" (WSL2 distro), "docker" (container).
The legacy value "mock" is accepted as an alias for "simulated". There is NO silent fallback: if a real
backend lacks a tool it raises ToolMissingError.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.orchestrator.config import FlowConfig
from services.orchestrator.eda.backends.base import EDABackend
from services.orchestrator.eda.backends.real import RealBackend
from services.orchestrator.eda.backends.simulated import SimulatedBackend
from services.orchestrator.eda.runner import DockerRunner, NativeRunner, WslRunner
from services.orchestrator.state import (
    DesignContract,
    FormalReport,
    LecReport,
    PPAMetrics,
    SimReport,
)

MODES = ("simulated", "native", "wsl", "docker")


def normalize_mode(mode: str) -> str:
    mode = (mode or "simulated").lower()
    return "simulated" if mode == "mock" else mode


def make_backend(mode: str, config: FlowConfig) -> EDABackend:
    mode = normalize_mode(mode)
    eda_cfg: dict[str, Any] = getattr(config, "eda", {}) or {}
    if mode == "simulated":
        return SimulatedBackend(telemetry_binary=config.telemetry_binary)
    if mode == "native":
        return RealBackend(NativeRunner(), engines=eda_cfg.get("engines"))
    if mode == "wsl":
        wsl = eda_cfg.get("wsl", {})
        runner = WslRunner(
            distro=wsl.get("distro", "Ubuntu-24.04"),
            env_scripts=wsl.get("env_scripts"),
            scratch=wsl.get("scratch", "$HOME/siliconforge-work"),
        )
        return RealBackend(
            runner,
            engines=eda_cfg.get("engines"),
            pnr_timeout_sec=float(eda_cfg.get("pnr_timeout_sec", 3600)),
            librelane_cmd=wsl.get("librelane_cmd"),
            timing_corner=str(config.pipeline.get("timing_corner", "worst")),
        )
    if mode == "docker":
        docker = eda_cfg.get("docker", {})
        return RealBackend(
            DockerRunner(image=docker.get("image", "siliconforge-eda:latest")),
            engines=eda_cfg.get("engines"),
        )
    raise ValueError(f"Unknown eda_mode '{mode}'. Expected one of {MODES}.")


class EDABridge:
    def __init__(self, config: FlowConfig, backend: EDABackend | None = None) -> None:
        self.config = config
        self.backend = backend or make_backend(config.eda_mode, config)

    @property
    def mode(self) -> str:
        return self.backend.name

    def check_tools(self) -> dict[str, bool]:
        return self.backend.check_tools()

    def run_formal_verification(
        self,
        run_dir: Path,
        contract: DesignContract,
        rtl_source: str,
        properties_ir: list[dict[str, Any]],
    ) -> tuple[FormalReport, str]:
        depth = int(self.config.pipeline.get("formal_bmc_depth", 20))
        return self.backend.run_formal(run_dir, contract, rtl_source, properties_ir, depth)

    def run_simulation(
        self, run_dir: Path, contract: DesignContract, rtl_source: str, testbench: Path | None = None
    ) -> SimReport:
        return self.backend.run_simulation(run_dir, contract, rtl_source, testbench)

    def run_synthesis_and_lec(
        self, run_dir: Path, contract: DesignContract, rtl_source: str
    ) -> LecReport:
        return self.backend.run_synthesis_and_lec(run_dir, contract, rtl_source, self.config.pdk)

    def run_physical_implementation(
        self,
        run_dir: Path,
        contract: DesignContract,
        rtl_source: str,
        knobs: dict[str, Any],
        episode_idx: int,
    ) -> PPAMetrics:
        return self.backend.run_physical_implementation(
            run_dir, contract, rtl_source, knobs, episode_idx, self.config.pdk
        )
