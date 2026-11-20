"""Pydantic schemas validating all structured LLM output (DesignContract, TypedPropertyIR).

These models are also exported as JSON Schema for constrained decoding (llama.cpp `json_schema`,
OpenAI `response_format`), so malformed output is rejected or prevented before it reaches the pipeline.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from services.orchestrator.state import DesignContract, PortSpec

IDENT = r"^[A-Za-z_][A-Za-z0-9_]*$"


class PortModel(BaseModel):
    name: str = Field(pattern=IDENT)
    direction: Literal["input", "output"]
    width: int = Field(ge=1, le=4096)
    description: str = ""


class ContractModel(BaseModel):
    module_name: str = Field(pattern=IDENT)
    clock_port: str = Field(pattern=IDENT)
    reset_port: str = Field(pattern=IDENT)
    reset_active_low: bool
    reset_sync: bool
    ports: list[PortModel] = Field(min_length=2)
    functional_requirements: list[str] = Field(default_factory=list)
    pipeline_stages: int = Field(default=1, ge=0, le=64)
    target_clock_ns: float = Field(default=10.0, gt=0)
    max_area_um2: float = Field(default=25000.0, gt=0)
    max_power_mw: float = Field(default=5.0, gt=0)

    @model_validator(mode="after")
    def _check_ports(self) -> ContractModel:
        names = [p.name for p in self.ports]
        if len(set(names)) != len(names):
            raise ValueError("duplicate port names in contract")
        by_name = {p.name: p for p in self.ports}
        for role, port in (("clock", self.clock_port), ("reset", self.reset_port)):
            p = by_name.get(port)
            if p is None:
                raise ValueError(f"{role}_port '{port}' is not declared in ports")
            if p.direction != "input" or p.width != 1:
                raise ValueError(f"{role}_port '{port}' must be a 1-bit input")
        return self

    def to_state(self) -> DesignContract:
        return DesignContract(
            module_name=self.module_name,
            clock_port=self.clock_port,
            reset_port=self.reset_port,
            reset_active_low=self.reset_active_low,
            reset_sync=self.reset_sync,
            ports=[PortSpec(p.name, p.direction, p.width, p.description) for p in self.ports],
            functional_requirements=list(self.functional_requirements),
            pipeline_stages=self.pipeline_stages,
            target_clock_ns=self.target_clock_ns,
            max_area_um2=self.max_area_um2,
            max_power_mw=self.max_power_mw,
        )


class _Prop(BaseModel):
    id: str = Field(pattern=IDENT)
    description: str = ""


class ResetValue(_Prop):
    kind: Literal["RESET_VALUE"]
    signal: str = Field(pattern=IDENT)
    expected: str


class Invariant(_Prop):
    kind: Literal["INVARIANT"]
    expression: str
    condition: str = "1'b1"


class HandshakeStability(_Prop):
    kind: Literal["HANDSHAKE_STABILITY"]
    valid_signal: str = Field(pattern=IDENT)
    ready_signal: str = Field(pattern=IDENT)
    data_signal: str = Field(pattern=IDENT)


class MutualExclusion(_Prop):
    kind: Literal["MUTUAL_EXCLUSION"]
    signals: list[str] = Field(min_length=2)


class CombinationalEquivalence(_Prop):
    kind: Literal["COMBINATIONAL_EQUIVALENCE"]
    expr_a: str
    expr_b: str
    condition: str = "1'b1"


class LatencyBound(_Prop):
    kind: Literal["LATENCY_BOUND"]
    trigger: str
    response: str
    bound: int = Field(ge=1, le=1024)


class CoverReachability(_Prop):
    kind: Literal["COVER_REACHABILITY"]
    expression: str


PropertyIR = Annotated[
    ResetValue
    | Invariant
    | HandshakeStability
    | MutualExclusion
    | CombinationalEquivalence
    | LatencyBound
    | CoverReachability,
    Field(discriminator="kind"),
]

PROPERTY_LIST = TypeAdapter(list[PropertyIR])


class EcoAdvice(BaseModel):
    recommendation: str
    knobs: dict[str, float | int | str] = Field(default_factory=dict)

    @field_validator("knobs")
    @classmethod
    def _non_empty_keys(cls, v: dict) -> dict:
        return {k: val for k, val in v.items() if k}


def contract_json_schema() -> dict:
    return ContractModel.model_json_schema()


def property_list_json_schema() -> dict:
    return PROPERTY_LIST.json_schema()
