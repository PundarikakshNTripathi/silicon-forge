"""Workspaces, the enforced model policy, the AI-usage ledger, the GPU guard and the Antigravity (`agy`) provider.

No test calls a live model or the real nvidia-smi: `agy` is replaced by a tiny Python script that speaks its protocol.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.orchestrator.config import InferenceConfig
from services.orchestrator.inference import gpu
from services.orchestrator.inference.catalog import Catalog, Target
from services.orchestrator.inference.cost_tracker import CostTracker
from services.orchestrator.inference.ledger import read_ledger, render_report, summarize
from services.orchestrator.inference.providers.agy import AgyProvider, with_effort
from services.orchestrator.inference.providers.base import (
    BaseLLMProvider,
    LLMResponse,
    ProviderError,
    TransientProviderError,
)
from services.orchestrator.inference.router import ModelRouter
from services.orchestrator.workspace import (
    Workspace,
    WorkspaceError,
    find_workspace,
    init_workspace,
    load_workspace,
)

# ------------------------------------------------------------------------------------------------ workspace


def test_workspace_without_manifest_uses_defaults_and_writes_nothing(tmp_path):
    ws = load_workspace(tmp_path)
    assert ws.name == tmp_path.name and not ws.has_manifest and ws.allowed_models == []
    assert list(tmp_path.iterdir()) == []


def test_init_writes_manifest_and_ignores_state_dir(tmp_path):
    ws = init_workspace(tmp_path / "chip", profile="competition", backend="wsl", pdk="ihp-sg13cmos5l",
                        allowed_models=["gemini-3.8-flash-*"], max_budget_usd=0)
    assert ws.has_manifest and ws.profile == "competition" and ws.allowed_models == ["gemini-3.8-flash-*"]
    assert ws.max_budget_usd == 0.0 and ws.pdk == "ihp-sg13cmos5l"
    assert ".siliconforge/" in (tmp_path / "chip" / ".gitignore").read_text()
    with pytest.raises(WorkspaceError, match="already exists"):
        init_workspace(tmp_path / "chip")
    init_workspace(tmp_path / "chip", profile="dev", overwrite=True)  # explicit overwrite is allowed


def test_find_workspace_walks_up_to_the_manifest(tmp_path):
    init_workspace(tmp_path, name="proj")
    nested = tmp_path / "src" / "rtl"
    nested.mkdir(parents=True)
    assert find_workspace(nested).root == tmp_path.resolve()
    assert find_workspace(nested).name == "proj"


def test_paths_cannot_escape_the_workspace(tmp_path):
    ws = Workspace(root=tmp_path)
    assert ws.resolve("rtl/top.sv") == (tmp_path / "rtl" / "top.sv").resolve()
    for bad in ("../outside.txt", "rtl/../../outside.txt", str(Path(tmp_path).parent / "x")):
        with pytest.raises(WorkspaceError, match="outside"):
            ws.resolve(bad)


def test_instruction_file_precedence(tmp_path):
    ws = Workspace(root=tmp_path)
    assert ws.instructions() == ("", None)
    (tmp_path / "AGENTS.md").write_text("agents rules")
    assert ws.instructions() == ("agents rules", "AGENTS.md")
    (tmp_path / "SILICONFORGE.md").write_text("forge rules")
    assert ws.instructions() == ("forge rules", "SILICONFORGE.md")


def test_bad_manifest_is_a_clear_error(tmp_path):
    (tmp_path / "siliconforge.yaml").write_text("- just\n- a list\n")
    with pytest.raises(WorkspaceError, match="mapping"):
        load_workspace(tmp_path)


def test_apply_points_config_at_the_workspace(tmp_path):
    ws = init_workspace(tmp_path, profile="competition", pdk="ihp-sg13cmos5l", allowed_models=["gemini-3.8-flash-*"])
    cfg = SimpleNamespace(workspace_root=None, eda_mode="simulated", pdk="sky130A",
                          inference=InferenceConfig(cache_path="x.sqlite"))
    ws.apply(cfg)
    assert cfg.workspace_root == ws.runs_dir and cfg.pdk == "ihp-sg13cmos5l"
    assert cfg.inference.profile == "competition" and cfg.inference.allowed_models == ["gemini-3.8-flash-*"]
    assert cfg.inference.ledger_path == str(ws.ledger_path)
    assert Path(cfg.inference.cache_path).parent == ws.state_dir


# --------------------------------------------------------------------------------------- model policy (router)


class Echo(BaseLLMProvider):
    local = False

    def __init__(self, name):
        self.name = name

    def generate(self, model, system_prompt, user_prompt, temperature=0.1, json_schema=None):
        return LLMResponse(content=f"{self.name}:{model}", prompt_tokens=10, completion_tokens=2, model=model,
                           provider=self.name)


def _catalog():
    chain = [Target("llamacpp", "local"), Target("cli_agy", "gemini-3.8-flash-high")]
    return Catalog(
        providers={"llamacpp": {"kind": "openai_compat", "local": True}, "cli_agy": {"kind": "cli_bridge"}},
        profiles={"dev": {"local_fast": chain, "balanced": chain, "frontier_reasoning": chain}},
    )


def _router(allowed, tmp_path, ledger=True):
    cfg = InferenceConfig(profile="dev", allowed_models=allowed, enable_semantic_cache=False,
                          ledger_path=str(tmp_path / "ledger.jsonl") if ledger else None)
    r = ModelRouter(cfg, CostTracker(), catalog=_catalog())
    r.provider_for = lambda name: Echo(name)  # type: ignore[method-assign]
    return r


def test_policy_drops_models_that_are_not_allowed(tmp_path):
    r = _router(["gemini-3.8-flash-*"], tmp_path)
    assert [t.model for t in r.chain("balanced")] == ["gemini-3.8-flash-high"]
    out = r.complete("rtl", "rtl_generation", "s", "u")
    assert out.content == "cli_agy:gemini-3.8-flash-high"  # the local model was never called


def test_policy_matches_provider_qualified_names_and_empty_means_open(tmp_path):
    assert [t.model for t in _router(["llamacpp:*"], tmp_path).chain("balanced")] == ["local"]
    assert len(_router([], tmp_path).chain("balanced")) == 2


def test_policy_that_forbids_everything_is_an_error_not_a_silent_fallback(tmp_path):
    r = _router(["claude-*"], tmp_path)
    with pytest.raises(ProviderError, match="policy forbids"):
        r.complete("rtl", "rtl_generation", "s", "u")


# ------------------------------------------------------------------------------------------------- ledger


def test_ledger_records_metadata_only_and_reports(tmp_path):
    r = _router(["gemini-3.8-flash-*"], tmp_path)
    r.complete("rtl", "rtl_generation", "SECRET system prompt", "SECRET user prompt")
    r.complete("formal", "counterexample_repair", "s2", "u2")
    path = tmp_path / "ledger.jsonl"
    raw = path.read_text()
    assert "SECRET" not in raw  # prompts are hashed, never stored
    rows = read_ledger(path)
    assert len(rows) == 2 and all(len(x["prompt_sha"]) == 16 for x in rows)
    s = summarize(rows)
    assert s["calls"] == 2 and "cli_agy:gemini-3.8-flash-high" in s["models"]
    md = render_report(rows, project="pemu", allowed_models=["gemini-3.8-flash-*"])
    assert "enforced in code" in md and "gemini-3.8-flash-high" in md and "Human-written section" in md


def test_ledger_survives_a_torn_final_line(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text(json.dumps({"ts": 1, "stage": "a", "provider": "x", "model": "m"}) + "\n{broken")
    assert len(read_ledger(p)) == 1


# ------------------------------------------------------------------------------------------------ GPU guard


def _snap(total, used, procs=()):
    return gpu.GpuSnapshot(name="Fake", total_mib=total, used_mib=used, processes=list(procs))


def _w(mib=5000):
    """Weights size in MiB, passed directly: tests must never allocate multi-GB files."""
    return {"weights_mib": mib}


def test_guard_refuses_when_vram_is_held_and_names_the_holder(tmp_path):
    snap = _snap(8151, 7365, [{"pid": 1, "name": "explorer.exe", "used_mib": None},
                              {"pid": 2, "name": "[Insufficient Permissions]", "used_mib": None},
                              {"pid": 32416, "name": "llama-server.exe", "used_mib": None}])
    v = gpu.can_load(None, 16384, "q8_0", 1, 99, snap=snap, **_w())
    assert not v.ok and v.free_mib == 786 and v.need_mib > 5000
    assert "llama-server.exe (pid 32416)" in v.reason and "explorer" not in v.reason
    assert "will not stop or evict" in v.reason and len(v.options) == 4


def test_guard_allows_when_free_and_for_cpu_runs(tmp_path):
    assert gpu.can_load(None, 8192, snap=_snap(16000, 1000), **_w()).ok
    assert gpu.can_load(None, 8192, gpu_layers=0, snap=_snap(8151, 8000), **_w()).ok  # CPU never needs VRAM
    assert gpu.can_load(None, 8192, snap=_snap(0, 0), **_w()).ok  # no NVIDIA GPU: llama.cpp picks another backend


def test_require_free_vram_raises_gpu_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu, "snapshot", lambda: _snap(8151, 7365))
    small = tmp_path / "m.gguf"
    small.write_bytes(b"x" * 1024)  # tiny placeholder; the real size is irrelevant to the refusal below
    monkeypatch.setattr(gpu, "estimate_vram_mib", lambda *a, **k: 7100)
    with pytest.raises(gpu.GpuBusyError, match="free of 8151"):
        gpu.require_free_vram(small, 16384, "q8_0", 1, 99)


def test_kv_cache_type_changes_the_estimate():
    est = {kv: gpu.estimate_vram_mib(None, 32768, kv, weights_mib=4000) for kv in ("f16", "q8_0", "q4_0")}
    assert est["f16"] > est["q8_0"] > est["q4_0"]


# ------------------------------------------------------------------------------------------- agy provider

FAKE_AGY = r'''
import json, sys
mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "ok"
req = json.loads(sys.stdin.readline())
assert req["event"] == "user", req
prompt = req["message"]["content"]
usage = {"input_tokens": 10400, "output_tokens": 7, "thinking_tokens": 3, "cache_read_tokens": 200}
if mode == "quota":
    res = {"status": "ERROR", "error": "429 RESOURCE_EXHAUSTED: quota exceeded", "usage": usage}
elif mode == "noresult":
    sys.exit(1)
else:
    res = {"status": "SUCCESS", "response": "echo:" + str(len(prompt)) + "\n", "usage": usage,
           "conversation_id": "c1", "duration_seconds": 1.5, "num_turns": 1}
    if "--json-schema" in sys.argv:
        res["structured_output"] = {"module": "cnt8", "width": 8}
print(json.dumps({"event": "init", "init": {}}))
print(json.dumps({"event": "result", "result": res}))
'''


@pytest.fixture
def fake_agy(tmp_path):
    script = tmp_path / "fake_agy.py"
    script.write_text(FAKE_AGY)
    wrapper = tmp_path / ("fake_agy.cmd" if sys.platform == "win32" else "fake_agy")
    if sys.platform == "win32":
        wrapper.write_text(f'@"{sys.executable}" "{script}" %*\r\n')
    else:
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        wrapper.chmod(0o755)
    return str(wrapper)


def test_agy_is_disabled_unless_opted_in(monkeypatch, fake_agy):
    monkeypatch.delenv("SILICONFORGE_ENABLE_CLI_BRIDGE", raising=False)
    p = AgyProvider(executable=fake_agy)
    assert not p.available()
    with pytest.raises(ProviderError, match="disabled"):
        p.generate("gemini-3.8-flash", "s", "u")


def test_agy_reads_real_usage_and_adds_the_effort_suffix(fake_agy):
    p = AgyProvider(enabled=True, executable=fake_agy)
    r = p.generate("gemini-3.8-flash", "SYS", "USR")
    sent = int(r.content.split(":")[1])
    assert sent > len("SYS") + len("USR")  # the no-tools preamble travels with every prompt
    assert r.model == "gemini-3.8-flash-medium"
    assert (r.prompt_tokens, r.completion_tokens, r.cached_tokens) == (10400, 10, 200)  # thinking counts as output
    assert r.extra["billing"] == "subscription" and r.extra["tokens_estimated"] is False


def test_agy_schema_returns_the_structured_output(fake_agy):
    p = AgyProvider(enabled=True, executable=fake_agy)
    r = p.generate("gemini-3.8-flash-high", "s", "u", json_schema={"type": "object"})
    assert json.loads(r.content) == {"module": "cnt8", "width": 8} and r.model == "gemini-3.8-flash-high"


def test_agy_argv_is_a_completion_call_with_no_prompt_on_the_command_line(fake_agy):
    argv = AgyProvider(enabled=True, executable=fake_agy).argv("agy", "gemini-3.8-flash-low", {"type": "object"})
    assert argv[:3] == ["agy", "--model", "gemini-3.8-flash-low"]
    assert "--sandbox" in argv and "--disable-slash-commands" in argv and "--json-schema" in argv
    assert argv[-2:] == ["--print", ""]  # the prompt travels on stdin, so no argv length limit


def test_agy_rate_limit_is_transient_so_the_router_can_fall_back(fake_agy, monkeypatch):
    p = AgyProvider(enabled=True, executable=fake_agy)
    monkeypatch.setattr(p, "argv", lambda exe, model, schema: [exe, "--mode", "quota"])
    with pytest.raises(TransientProviderError, match="RESOURCE_EXHAUSTED"):
        p.generate("gemini-3.8-flash", "s", "u")


def test_agy_without_a_result_event_fails_cleanly(fake_agy, monkeypatch):
    p = AgyProvider(enabled=True, executable=fake_agy)
    monkeypatch.setattr(p, "argv", lambda exe, model, schema: [exe, "--mode", "noresult"])
    with pytest.raises(ProviderError):
        p.generate("gemini-3.8-flash", "s", "u")


def test_with_effort_helper():
    assert with_effort("gemini-3.8-flash") == "gemini-3.8-flash-medium"
    assert with_effort("gemini-3.8-flash-high") == "gemini-3.8-flash-high"
    assert with_effort("gemini-3.8-flash", "low") == "gemini-3.8-flash-low"


def test_agy_retries_a_transient_failure_once_then_succeeds(fake_agy, monkeypatch):
    monkeypatch.setattr("services.orchestrator.inference.providers.base.time.sleep", lambda s: None)
    p = AgyProvider(enabled=True, executable=fake_agy)
    calls = {"n": 0}
    real = p._generate_once

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientProviderError("cli:agy: timed out")
        return real(*a, **k)

    monkeypatch.setattr(p, "_generate_once", flaky)
    assert p.generate("gemini-3.8-flash", "s", "u").content.startswith("echo:") and calls["n"] == 2


def test_workspace_can_raise_the_token_ceiling_for_subscription_models(tmp_path):
    """agy calls carry ~10k tokens of overhead each, so the default 500k ceiling stops a long agent session."""
    (tmp_path / "siliconforge.yaml").write_text(
        "name: x" + chr(10) + "policy:" + chr(10) + "  max_total_tokens: 20000000" + chr(10))
    ws = load_workspace(tmp_path)
    cfg = SimpleNamespace(workspace_root=None, eda_mode="simulated", pdk="p", inference=InferenceConfig())
    assert InferenceConfig().max_total_tokens == 500_000
    ws.apply(cfg)
    assert cfg.inference.max_total_tokens == 20_000_000 and ws.describe()["max_total_tokens"] == 20_000_000
