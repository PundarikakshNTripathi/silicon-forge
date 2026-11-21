"""Gemini through the Antigravity CLI (`agy`), driven in print mode. OPT-IN, same rules as the other CLI bridges.

Why this exists: a Google AI subscription includes Gemini 3.8 Flash inside Antigravity but not an API key. `agy` is the
official CLI for it. We launch it as a subprocess, one call per request, and never read, copy or store its credentials.
Whether driving the official CLI from another program is allowed under your plan is a grey area; check the terms.

How a call works (verified against agy 1.5.13):
  * the prompt goes in as one NDJSON line on stdin (`--input-format stream-json`), so there is no argv length limit;
  * `--output-format stream-json` yields `step_update` events and one final `result` event with real token usage;
  * `--json-schema` constrains the final answer and is returned as `structured_output`;
  * the model id carries the reasoning effort (`gemini-3.8-flash-low|medium|high`);
  * agy is a full tool-using agent (it can run commands and edit files). A completion call must not do that, so each
    call runs in a fresh empty temp directory, with a "no tools" instruction, and is cleaned up afterwards.
Every call carries ~10k tokens of agent overhead; that is reported honestly in `prompt_tokens`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from services.orchestrator.inference.providers.base import (
    BaseLLMProvider,
    LLMResponse,
    ProviderError,
    RefusalError,
    TransientProviderError,
    retry_call,
)
from services.orchestrator.inference.providers.cli_bridge import (
    ENABLE_ENV,
    TOS_WARNING,
    bridge_enabled,
)

EFFORTS = ("low", "medium", "high")
_TRANSIENT_MARKERS = ("quota", "rate limit", "rate-limit", "429", "resource exhausted", "overloaded", "unavailable",
                      "try again", "timed out", "timeout", "deadline", "503", "500", "internal error")
_REFUSAL_MARKERS = ("safety", "blocked", "refus", "policy")

NO_TOOLS_PREAMBLE = (
    "You are acting as a plain text-completion function. Do NOT use any tools: no shell commands, no file reads or "
    "writes, no browsing, no subagents. Do not ask questions. Answer directly with only the requested output."
)


def find_agy() -> str | None:
    found = shutil.which("agy")
    if found:
        return found
    local = os.getenv("LOCALAPPDATA")
    cands = [Path(local) / "agy" / "bin" / "agy.exe"] if local else []
    cands.append(Path.home() / ".local" / "bin" / "agy")
    return next((str(p) for p in cands if p.exists()), None)


def with_effort(model: str, default_effort: str = "medium") -> str:
    """`gemini-3.8-flash` -> `gemini-3.8-flash-medium`; ids that already carry an effort are unchanged."""
    return model if model.rsplit("-", 1)[-1] in EFFORTS else f"{model}-{default_effort}"


class AgyProvider(BaseLLMProvider):
    local = False  # billed to the subscription, tracked separately from metered cost
    _slots: threading.BoundedSemaphore | None = None
    _slots_lock = threading.Lock()

    def __init__(self, timeout_s: float = 420.0, enabled: bool = False, default_effort: str = "medium",
                 max_concurrency: int = 2, executable: str | None = None, sandbox: bool = True) -> None:
        self.name = "cli:agy"
        self.timeout_s = timeout_s
        self.enabled = bridge_enabled(enabled)
        self.default_effort = default_effort
        self.executable = executable
        self.sandbox = sandbox
        with AgyProvider._slots_lock:  # shared across instances so parallel episodes cannot flood the CLI
            if AgyProvider._slots is None:
                AgyProvider._slots = threading.BoundedSemaphore(max(1, max_concurrency))

    # ---------------------------------------------------------------------------------------------

    def _exe(self) -> str | None:
        return self.executable or find_agy()

    def available(self) -> bool:
        return self.enabled and self._exe() is not None

    def health(self) -> dict:
        return {"ok": self.available(), "enabled": self.enabled, "installed": self._exe() is not None}

    def argv(self, exe: str, model: str, json_schema: dict | None) -> list[str]:
        a = [exe, "--model", with_effort(model, self.default_effort), "--input-format", "stream-json",
             "--output-format", "stream-json", "--disable-slash-commands"]
        if self.sandbox:
            a.append("--sandbox")
        if json_schema is not None:
            a += ["--json-schema", json.dumps(json_schema)]
        return a + ["--print", ""]

    @staticmethod
    def parse_result(stdout: str) -> dict:
        """The last `result` event of the NDJSON stream."""
        result = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "result":
                result = ev.get("result") or {}
        if result is None:
            raise ProviderError("agy produced no result event")
        return result

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None) -> LLMResponse:
        """One retry on a transient failure (a hung call, a rate limit): a single stuck `agy` process should not end a session."""
        return retry_call(lambda: self._generate_once(model, system_prompt, user_prompt, json_schema),
                          attempts=2, base_delay=3.0)

    def _generate_once(self, model, system_prompt, user_prompt, json_schema=None) -> LLMResponse:
        if not self.enabled:
            raise ProviderError(f"CLI bridge is disabled. {TOS_WARNING} Enable it in the UI or set {ENABLE_ENV}=1.")
        exe = self._exe()
        if exe is None:
            raise ProviderError("The Antigravity CLI (`agy`) is not installed or not on PATH.")
        prompt = f"{NO_TOOLS_PREAMBLE}\n\n{system_prompt}\n\n---\n\n{user_prompt}"
        line = json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"
        assert AgyProvider._slots is not None
        t0 = time.perf_counter()
        with AgyProvider._slots, tempfile.TemporaryDirectory(prefix="sf_agy_") as cwd:
            try:
                proc = subprocess.run(
                    self.argv(exe, model, json_schema), input=line, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=self.timeout_s, check=False, cwd=cwd,
                )
            except subprocess.TimeoutExpired as exc:
                raise TransientProviderError(f"{self.name}: timed out after {self.timeout_s:.0f}s") from exc
        latency = round(time.perf_counter() - t0, 3)
        try:
            res = self.parse_result(proc.stdout)
        except ProviderError:
            raise self._classify(f"exit {proc.returncode}: {proc.stderr.strip()[:300]}") from None
        if res.get("status") != "SUCCESS":
            raise self._classify(str(res.get("error") or res.get("finished_reason") or res.get("status")))
        usage = res.get("usage") or {}
        if json_schema is not None and res.get("structured_output") is not None:
            content = json.dumps(res["structured_output"])
        else:
            content = str(res.get("response", "")).strip()
        if not content:
            raise TransientProviderError(f"{self.name}: empty response")
        cached = int(usage.get("cache_read_tokens", 0))
        completion = int(usage.get("output_tokens", 0)) + int(usage.get("thinking_tokens", 0))
        return LLMResponse(
            content=content,
            prompt_tokens=int(usage.get("input_tokens", 0)),
            completion_tokens=completion,
            cached_tokens=cached,
            model=with_effort(model, self.default_effort),
            latency_s=latency,
            provider=self.name,
            extra={"billing": "subscription", "tokens_estimated": False, "agy_overhead_tokens": True,
                   "conversation_id": res.get("conversation_id"), "agy_seconds": res.get("duration_seconds"),
                   "num_turns": res.get("num_turns")},
        )

    def _classify(self, message: str) -> ProviderError:
        low = message.lower()
        if any(m in low for m in _TRANSIENT_MARKERS):
            return TransientProviderError(f"{self.name}: {message[:300]}")
        if any(m in low for m in _REFUSAL_MARKERS):
            return RefusalError(f"{self.name}: {message[:300]}")
        return ProviderError(f"{self.name}: {message[:300]}")
