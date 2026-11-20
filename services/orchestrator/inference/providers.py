"""Unified LLM provider abstraction with deterministic MockLLM for zero-key testing."""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    model: str


class BaseLLMProvider:
    def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        raise NotImplementedError


class OpenAICompatibleProvider(BaseLLMProvider):
    """Works with OpenAI, vLLM, Ollama (/v1/chat/completions), or any OpenAI-compatible gateway."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")

    def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        json_schema: dict | None = None,
    ) -> LLMResponse:
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


class ReplayMissError(RuntimeError):
    """No recorded fixture matches the request (the replay provider never invents designs)."""


class ReplayProvider(BaseLLMProvider):
    """Deterministic provider that replays recorded fixtures from `hardware/examples/*/fixtures/`.

    Used for CI, offline demos and tests. It only serves the bundled example designs; for any other spec it
    raises ReplayMissError instead of returning someone else's design. Responses are real files that are
    separately verified against real EDA tools (see `scripts/verify_examples.py`).
    """

    def __init__(self, examples_dir: Path | None = None, inject_initial_bug: bool = False) -> None:
        root = Path(__file__).resolve().parents[3]
        self.examples_dir = examples_dir or (root / "hardware" / "examples")
        self.inject_initial_bug = inject_initial_bug
        self._rtl_calls = 0
        self._designs = self._index()

    def _index(self) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for fx in sorted(self.examples_dir.glob("*/fixtures/contract.json")):
            module = json.loads(fx.read_text(encoding="utf-8"))["module_name"]
            out[module] = fx.parent.parent
        return out

    def designs(self) -> list[str]:
        return sorted(self._designs)

    def _match(self, text: str) -> Path:
        for module, path in self._designs.items():
            if re.search(rf"\b{re.escape(module)}\b", text):
                return path
        raise ReplayMissError(
            "No replay fixture matches this specification. The replay provider only serves the bundled "
            f"examples ({', '.join(self.designs())}). Select a real model profile (local llama.cpp or a cloud "
            "provider) to design arbitrary specs."
        )

    def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        json_schema: dict | None = None,
    ) -> LLMResponse:
        first_line = system_prompt.strip().splitlines()[0].lower() if system_prompt.strip() else ""
        if "micro-architect" in first_line:
            content = (self._match(user_prompt) / "fixtures" / "contract.json").read_text(encoding="utf-8")
        elif "formal verification" in first_line:
            content = (self._match(user_prompt) / "fixtures" / "props.json").read_text(encoding="utf-8")
        elif "rtl design engineer" in first_line:
            self._rtl_calls += 1
            design = self._match(user_prompt)
            bug = design / "fixtures" / "rtl_bug.sv"
            if self.inject_initial_bug and self._rtl_calls == 1 and "REPAIR" not in user_prompt and bug.exists():
                content = bug.read_text(encoding="utf-8")
            else:
                content = next((design / "rtl").glob("*.sv")).read_text(encoding="utf-8")
        else:
            content = json.dumps({
                "recommendation": "Relax the clock period or use DELAY synthesis with lower core utilization.",
                "knobs": {"SYNTH_STRATEGY": "DELAY 4", "FP_CORE_UTIL": 40, "PL_TARGET_DENSITY_PCT": 55},
            })
        return LLMResponse(
            content=content,
            prompt_tokens=max(64, len(system_prompt + user_prompt) // 4),
            completion_tokens=max(32, len(content) // 4),
            model=f"replay:{model}",
        )


# Back-compat alias for older imports/tests.
MockChipDesignLLM = ReplayProvider
