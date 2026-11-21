"""Opt-in bridge to the OFFICIAL provider CLIs you are already signed into (`claude -p`, `codex exec`, `gemini -p`).

Usage is billed by your subscription, not per token, so cost is recorded as $0 and tokens are estimated.

Compliance: this launches the vendor's own CLI as a subprocess. It NEVER reads, copies or stores OAuth tokens, and it
does not call any provider endpoint with subscription credentials. Anthropic and Google prohibit reusing subscription
OAuth tokens in third-party tools, and whether driving their official CLI from another program is allowed is a grey
area that can change: check your plan's current terms before enabling. It is OFF by default.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

from services.orchestrator.inference.providers.base import (
    BaseLLMProvider,
    LLMResponse,
    ProviderError,
    TransientProviderError,
)

ENABLE_ENV = "SILICONFORGE_ENABLE_CLI_BRIDGE"

# command templates: the combined prompt is sent on stdin to avoid argv length limits and shell quoting issues
DEFAULT_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "-p", "--output-format", "text"],
    "codex": ["codex", "exec", "-"],
    "gemini": ["gemini", "-p", ""],
}

TOS_WARNING = (
    "CLI bridge enabled: SiliconForge will run your locally installed official CLI. It never reads provider OAuth "
    "tokens. Check your subscription terms; Anthropic and Google restrict third-party use of subscription auth."
)


def bridge_enabled(config_flag: bool = False) -> bool:
    return config_flag or os.getenv(ENABLE_ENV, "") in ("1", "true", "yes")


class CliBridgeProvider(BaseLLMProvider):
    local = False  # billed to a subscription, tracked separately from metered cost

    def __init__(self, cli: str = "claude", command: list[str] | None = None, timeout_s: float = 600.0,
                 enabled: bool = False) -> None:
        if cli not in DEFAULT_COMMANDS and command is None:
            raise ProviderError(f"Unknown CLI '{cli}'. Supported: {', '.join(DEFAULT_COMMANDS)}")
        self.cli = cli
        self.name = f"cli:{cli}"
        self.command = command or DEFAULT_COMMANDS[cli]
        self.timeout_s = timeout_s
        self.enabled = bridge_enabled(enabled)

    def available(self) -> bool:
        return self.enabled and shutil.which(self.command[0]) is not None

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None) -> LLMResponse:
        if not self.enabled:
            raise ProviderError(f"CLI bridge is disabled. {TOS_WARNING} Enable it in the UI or set {ENABLE_ENV}=1.")
        if shutil.which(self.command[0]) is None:
            raise ProviderError(f"'{self.command[0]}' is not installed or not on PATH.")
        prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                self.command, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout_s, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TransientProviderError(f"{self.name}: timed out after {self.timeout_s:.0f}s") from exc
        if proc.returncode != 0:
            raise ProviderError(f"{self.name}: exit {proc.returncode}: {proc.stderr.strip()[:500]}")
        out = proc.stdout.strip()
        return LLMResponse(
            content=out,
            prompt_tokens=max(1, len(prompt) // 4),
            completion_tokens=max(1, len(out) // 4),
            model=model or self.cli,
            latency_s=round(time.perf_counter() - t0, 3),
            provider=self.name,
            extra={"billing": "subscription", "tokens_estimated": True},
        )

    def health(self) -> dict:
        return {"ok": self.available(), "enabled": self.enabled, "installed": shutil.which(self.command[0]) is not None}
