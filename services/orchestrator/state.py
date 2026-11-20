"""Core domain models and pipeline state for SiliconForge."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class PipelineStage(str, Enum):
    INIT = "INIT"
    ARCHITECT = "ARCHITECT"
    RTL_GEN = "RTL_GEN"
    GUARDRAIL_AUDIT = "GUARDRAIL_AUDIT"
    FORMAL_VERIFY = "FORMAL_VERIFY"
    SYNTHESIS_LEC = "SYNTHESIS_LEC"
    PNR_RL_OPT = "PNR_RL_OPT"
    SIGNOFF = "SIGNOFF"
    FAILED = "FAILED"


@dataclass
class PortSpec:
    name: str
    direction: str  # "input" or "output"
    width: int
    description: str = ""


@dataclass
class DesignContract:
    module_name: str
    clock_port: str
    reset_port: str
    reset_active_low: bool
    reset_sync: bool
    ports: List[PortSpec]
    functional_requirements: List[str]
    pipeline_stages: int = 1
    target_clock_ns: float = 10.0
    max_area_um2: float = 25000.0
    max_power_mw: float = 5.0

    def port_names(self) -> set[str]:
        return {p.name for p in self.ports}


@dataclass
class GuardrailViolation:
    gate: str
    rule_id: str
    severity: str  # "ERROR" | "WARNING"
    message: str
    line_number: int = 0


@dataclass
class GuardrailReport:
    passed: bool
    violations: List[GuardrailViolation] = field(default_factory=list)


@dataclass
class FormalWitness:
    property_id: str
    task: str  # "bmc" | "prove" | "cover"
    cycle: int
    failing_signal: str
    stimulus: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""


@dataclass
class FormalReport:
    bmc_passed: bool
    prove_passed: bool
    cover_passed: bool
    vacuous_detected: bool
    witnesses: List[FormalWitness] = field(default_factory=list)
    sby_log: str = ""

    @property
    def passed(self) -> bool:
        return self.bmc_passed and self.prove_passed and self.cover_passed and not self.vacuous_detected


@dataclass
class PPAMetrics:
    wns_ns: float
    tns_ns: float
    area_um2: float
    total_power_mw: float
    cell_count: int
    drc_violations: int
    lvs_clean: bool
    runtime_sec: float = 0.0
    knobs: Dict[str, Any] = field(default_factory=dict)
    reward: float = 0.0


@dataclass
class FlowState:
    run_id: str
    spec_text: str
    stage: PipelineStage = PipelineStage.INIT
    contract: Optional[DesignContract] = None
    rtl_source: str = ""
    formal_properties_ir: List[Dict[str, Any]] = field(default_factory=list)
    sva_source: str = ""
    guardrail_history: List[GuardrailReport] = field(default_factory=list)
    formal_history: List[FormalReport] = field(default_factory=list)
    lec_passed: bool = False
    ppa_episodes: List[PPAMetrics] = field(default_factory=list)
    pareto_front: List[PPAMetrics] = field(default_factory=list)
    best_ppa: Optional[PPAMetrics] = None
    gds_path: Optional[str] = None
    signoff_passed: bool = False
    error_message: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)

    def log_event(self, stage: str, status: str, detail: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.events.append({
            "stage": stage,
            "status": status,
            "detail": detail,
            "payload": payload or {},
        })

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
