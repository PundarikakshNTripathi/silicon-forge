"""EDA backend interface. Every result carries `mode` provenance; missing tools raise, never fall back."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from services.orchestrator.eda.errors import ToolMissingError
from services.orchestrator.state import (
    DesignContract,
    FormalReport,
    LecReport,
    PPAMetrics,
    SimReport,
)


class EDABackend(ABC):
    """One execution environment for the verification + implementation flow."""

    name = "abstract"
    #: tool names required per capability, checked by `require`
    REQUIRED: dict[str, list[str]] = {}

    @abstractmethod
    def check_tools(self) -> dict[str, bool]:
        """Return availability for every tool this backend can use."""

    def require(self, capability: str) -> None:
        needed = self.REQUIRED.get(capability, [])
        have = self.check_tools()
        missing = [t for t in needed if not have.get(t, False)]
        if missing:
            raise ToolMissingError(self.name, missing, self.install_hint())

    def install_hint(self) -> str:
        return "Run `python scripts/dev.py doctor` for setup guidance, or use --backend simulated for demos."

    @abstractmethod
    def run_formal(
        self,
        run_dir: Path,
        contract: DesignContract,
        rtl_source: str,
        properties_ir: list[dict[str, Any]],
        depth: int,
    ) -> tuple[FormalReport, str]:
        """Return (report, sva_block)."""

    @abstractmethod
    def run_simulation(
        self, run_dir: Path, contract: DesignContract, rtl_source: str, testbench: Path | None
    ) -> SimReport:
        """Lint/simulate the RTL."""

    @abstractmethod
    def run_synthesis_and_lec(
        self, run_dir: Path, contract: DesignContract, rtl_source: str, pdk: str
    ) -> LecReport:
        """Synthesize and prove RTL == gate-level netlist."""

    @abstractmethod
    def run_physical_implementation(
        self,
        run_dir: Path,
        contract: DesignContract,
        rtl_source: str,
        knobs: dict[str, Any],
        episode_idx: int,
        pdk: str,
    ) -> PPAMetrics:
        """One P&R episode with the given knobs."""
