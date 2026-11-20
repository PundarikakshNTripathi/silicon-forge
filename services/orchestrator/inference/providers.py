"""Unified LLM provider abstraction with deterministic MockLLM for zero-key testing."""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    model: str


class BaseLLMProvider:
    def generate(self, model: str, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> LLMResponse:
        raise NotImplementedError


class OpenAICompatibleProvider(BaseLLMProvider):
    """Works with OpenAI, vLLM, Ollama (/v1/chat/completions), or any OpenAI-compatible gateway."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")

    def generate(self, model: str, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> LLMResponse:
        payload = json.dumps({
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        msg = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return LLMResponse(
            content=msg,
            prompt_tokens=int(usage.get("prompt_tokens", len(system_prompt + user_prompt) // 4)),
            completion_tokens=int(usage.get("completion_tokens", len(msg) // 4)),
            model=model,
        )


class MockChipDesignLLM(BaseLLMProvider):
    """Deterministic principal-level hardware LLM simulator for local testing & CI.

    Demonstrates full contract synthesis, intentional first-pass bug + formal repair loop,
    and Typed Property IR generation without external API keys.
    """

    def __init__(self, inject_initial_bug: bool = False) -> None:
        self.inject_initial_bug = inject_initial_bug
        self._rtl_calls = 0

    def generate(self, model: str, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> LLMResponse:
        first_line = system_prompt.strip().splitlines()[0].lower() if system_prompt.strip() else ""
        if "micro-architect" in first_line:
            content = json.dumps({
                "module_name": "axi_lite_alu",
                "clock_port": "clk",
                "reset_port": "rst_n",
                "reset_active_low": True,
                "reset_sync": False,
                "ports": [
                    {"name": "clk", "direction": "input", "width": 1, "description": "System clock"},
                    {"name": "rst_n", "direction": "input", "width": 1, "description": "Active-low async reset"},
                    {"name": "valid_i", "direction": "input", "width": 1, "description": "Input request valid"},
                    {"name": "ready_o", "direction": "output", "width": 1, "description": "Input request ready"},
                    {"name": "op_i", "direction": "input", "width": 3, "description": "ALU opcode (0:ADD,1:SUB,2:AND,3:OR,4:XOR)"},
                    {"name": "a_i", "direction": "input", "width": 32, "description": "Operand A"},
                    {"name": "b_i", "direction": "input", "width": 32, "description": "Operand B"},
                    {"name": "valid_o", "direction": "output", "width": 1, "description": "Output result valid"},
                    {"name": "result_o", "direction": "output", "width": 32, "description": "Registered 32-bit ALU result"},
                    {"name": "zero_o", "direction": "output", "width": 1, "description": "High when result_o == 0"},
                ],
                "functional_requirements": [
                    "Single-cycle registered ALU with valid/ready handshake",
                    "Supports ADD, SUB, AND, OR, XOR opcodes with zero flag",
                    "Asynchronous active-low reset clears valid_o and result_o to 0",
                ],
                "pipeline_stages": 1,
                "target_clock_ns": 10.0,
                "max_area_um2": 25000.0,
                "max_power_mw": 5.0,
            }, indent=2)
        elif "formal verification" in first_line:
            content = json.dumps([
                {
                    "id": "P_RST_VALID_LOW",
                    "kind": "RESET_VALUE",
                    "signal": "valid_o",
                    "expected": "1'b0",
                    "description": "valid_o must be 0 immediately after reset",
                },
                {
                    "id": "P_RST_RESULT_ZERO",
                    "kind": "RESET_VALUE",
                    "signal": "result_o",
                    "expected": "32'd0",
                    "description": "result_o must be 0 immediately after reset",
                },
                {
                    "id": "P_ZERO_FLAG_CONSISTENT",
                    "kind": "INVARIANT",
                    "condition": "valid_o",
                    "expression": "(zero_o == (result_o == 32'd0))",
                    "description": "zero_o must match (result_o == 0) whenever valid_o is asserted",
                },
                {
                    "id": "C_REACH_VALID_OUT",
                    "kind": "COVER_REACHABILITY",
                    "expression": "valid_o && (result_o != 32'd0)",
                    "description": "Prove non-vacuous reachability of a non-zero valid ALU result",
                },
            ], indent=2)
        elif "rtl design engineer" in first_line:
            self._rtl_calls += 1
            if self.inject_initial_bug and self._rtl_calls == 1 and "REPAIR" not in user_prompt:
                # Deliberately inject a subtle zero_o polarity bug to exercise formal repair loop
                zero_expr = "(alu_comb != 32'd0)"
            else:
                zero_expr = "(alu_comb == 32'd0)"
            content = f"""module axi_lite_alu (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        valid_i,
    output logic        ready_o,
    input  logic [2:0]  op_i,
    input  logic [31:0] a_i,
    input  logic [31:0] b_i,
    output logic        valid_o,
    output logic [31:0] result_o,
    output logic        zero_o
);
    logic [31:0] alu_comb;

    assign ready_o = 1'b1;

    always_comb begin
        alu_comb = 32'd0;
        case (op_i)
            3'd0:    alu_comb = a_i + b_i;
            3'd1:    alu_comb = a_i - b_i;
            3'd2:    alu_comb = a_i & b_i;
            3'd3:    alu_comb = a_i | b_i;
            3'd4:    alu_comb = a_i ^ b_i;
            default: alu_comb = 32'd0;
        endcase
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_o  <= 1'b0;
            result_o <= 32'd0;
            zero_o   <= 1'b1;
        end else begin
            valid_o <= valid_i & ready_o;
            if (valid_i & ready_o) begin
                result_o <= alu_comb;
                zero_o   <= {zero_expr};
            end
        end
    end
endmodule
"""
        else:
            content = json.dumps({
                "recommendation": "Use DELAY 4 synthesis and 55% placement density for timing closure.",
                "knobs": {"SYNTH_STRATEGY": "DELAY 4", "FP_CORE_UTIL": 45, "PL_TARGET_DENSITY_PCT": 55},
            })

        return LLMResponse(
            content=content,
            prompt_tokens=max(64, len(system_prompt + user_prompt) // 4),
            completion_tokens=max(32, len(content) // 4),
            model=model,
        )
