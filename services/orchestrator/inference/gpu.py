"""GPU arbitration: SiliconForge shares the machine with other projects, so it asks before it loads anything.

Everything here is read-only. We query VRAM, look for other OpenAI-compatible model servers on well-known ports, and
decide whether our own llama.cpp server fits in what is actually free. We never stop, kill or attach to a process
that is not ours; when the GPU is busy the answer is an explanation plus options (wait, use a cloud/subscription
model, or run on CPU), not an eviction.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx

#: ports where other tools commonly serve a local model (llama.cpp, LM Studio, Ollama, vLLM)
KNOWN_PORTS = (8080, 8000, 1234, 11434, 8189)
#: per-token KV cache cost in MiB for a ~9B dense model; coarse but conservative, scaled by cache type
_KV_MIB_PER_TOKEN = {"f16": 0.11, "q8_0": 0.06, "q4_0": 0.035}
_RUNTIME_OVERHEAD_MIB = 700  # CUDA context, compute buffers, scratch


class GpuBusyError(RuntimeError):
    """Not enough free VRAM for the requested server. The message lists what to do instead."""


@dataclass
class GpuSnapshot:
    name: str = ""
    total_mib: int = 0
    used_mib: int = 0
    driver: str = ""
    processes: list[dict] = field(default_factory=list)

    @property
    def free_mib(self) -> int:
        return max(0, self.total_mib - self.used_mib)

    @property
    def present(self) -> bool:
        return self.total_mib > 0

    def to_dict(self) -> dict:
        return {"name": self.name, "total_mib": self.total_mib, "used_mib": self.used_mib, "free_mib": self.free_mib,
                "driver": self.driver, "processes": self.processes}


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, timeout=10, check=False).stdout


def snapshot() -> GpuSnapshot:
    """Current VRAM and compute processes; an empty snapshot when there is no NVIDIA GPU."""
    if shutil.which("nvidia-smi") is None:
        return GpuSnapshot()
    try:
        row = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
                    "--format=csv,noheader,nounits"]).strip().splitlines()[0]
        name, total, used, driver = [x.strip() for x in row.split(",")]
        snap = GpuSnapshot(name=name, total_mib=int(total), used_mib=int(used), driver=driver)
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return GpuSnapshot()
    try:
        for line in _run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                          "--format=csv,noheader,nounits"]).strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3 and parts[0].isdigit():
                snap.processes.append({"pid": int(parts[0]), "name": Path(parts[1]).name,
                                       "used_mib": int(parts[2]) if parts[2].isdigit() else None})
    except (OSError, subprocess.SubprocessError):
        pass
    return snap


def foreign_servers(own_port: int | None = None, ports: tuple[int, ...] = KNOWN_PORTS, timeout: float = 0.4) -> list[dict]:
    """OpenAI-compatible servers already listening on this machine (read-only probe of GET /v1/models)."""
    found = []
    for port in ports:
        url = f"http://127.0.0.1:{port}"
        try:
            r = httpx.get(f"{url}/v1/models", timeout=timeout)
        except httpx.HTTPError:
            continue
        if r.status_code != 200:
            continue
        try:
            data = r.json()
            names = [m.get("id") for m in (data.get("data") or data.get("models") or []) if isinstance(m, dict)]
        except ValueError:
            names = []
        found.append({"url": url, "port": port, "models": [n for n in names if n], "ours": port == own_port})
    return found


def estimate_vram_mib(model_path: Path | None, ctx: int, kv_type: str = "q8_0", parallel: int = 1,
                      gpu_layers: int = 99, weights_mib: float | None = None) -> int:
    """Rough VRAM need: weights (scaled by offloaded share when partial) + KV cache + runtime overhead."""
    weights = weights_mib if weights_mib is not None else model_path.stat().st_size / (1024 * 1024)  # type: ignore[union-attr]
    share = 1.0 if gpu_layers >= 99 else min(1.0, max(0.0, gpu_layers / 40))
    kv = _KV_MIB_PER_TOKEN.get(kv_type, 0.11) * ctx * max(1, parallel) * share
    return int(weights * share + kv + _RUNTIME_OVERHEAD_MIB)


_INFERENCE_HINTS = ("llama", "ollama", "lmstudio", "lm studio", "vllm", "python", "comfy", "koboldcpp", "text-generation")
_DESKTOP_NOISE = ("explorer.exe", "dwm.exe", "searchhost.exe", "startmenuexperiencehost.exe")


def likely_holders(snap: GpuSnapshot) -> list[dict]:
    """The processes most likely responsible for VRAM use: inference runtimes first, desktop shell noise dropped."""
    usable = [p for p in snap.processes if not p["name"].startswith("[") and p["name"].lower() not in _DESKTOP_NOISE]
    hinted = [p for p in usable if any(h in p["name"].lower() for h in _INFERENCE_HINTS)]
    return hinted or usable


@dataclass
class GpuVerdict:
    ok: bool
    need_mib: int
    free_mib: int
    reason: str = ""
    options: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "need_mib": self.need_mib, "free_mib": self.free_mib, "reason": self.reason,
                "options": self.options}


def can_load(model_path: Path | None, ctx: int, kv_type: str = "q8_0", parallel: int = 1, gpu_layers: int = 99,
             snap: GpuSnapshot | None = None, headroom_mib: int = 256, weights_mib: float | None = None) -> GpuVerdict:
    """Would our server fit in the VRAM that is free right now?"""
    snap = snap or snapshot()
    need = estimate_vram_mib(model_path, ctx, kv_type, parallel, gpu_layers, weights_mib)
    if gpu_layers == 0:
        return GpuVerdict(True, 0, snap.free_mib, "CPU only: no VRAM needed")
    if not snap.present:
        return GpuVerdict(True, need, 0, "No NVIDIA GPU detected; llama.cpp will use the CPU or Vulkan")
    if snap.free_mib >= need + headroom_mib:
        return GpuVerdict(True, need, snap.free_mib)
    holders = ", ".join(f"{p['name']} (pid {p['pid']})" for p in likely_holders(snap)) or "another process"
    return GpuVerdict(
        False, need, snap.free_mib,
        f"The GPU has {snap.free_mib} MiB free of {snap.total_mib} MiB (held by {holders}) but this model needs "
        f"about {need} MiB. SiliconForge will not stop or evict other programs.",
        [
            "Wait for the other job to finish, or stop it yourself and retry.",
            "Use a hosted or subscription model for this run (profiles: gemini-agy, hybrid, cloud).",
            "Run on the CPU (gpu_layers = 0): slower, but it does not touch the GPU.",
            "Lower the context length or use a smaller quantization to shrink the footprint.",
        ],
    )


def require_free_vram(model_path: Path, ctx: int, kv_type: str, parallel: int, gpu_layers: int) -> GpuVerdict:
    verdict = can_load(model_path, ctx, kv_type, parallel, gpu_layers)
    if not verdict.ok:
        raise GpuBusyError(verdict.reason + " Options: " + " ".join(f"({i}) {o}" for i, o in enumerate(verdict.options, 1)))
    return verdict
