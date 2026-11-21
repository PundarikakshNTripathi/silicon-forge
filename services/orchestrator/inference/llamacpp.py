"""Local llama.cpp manager: install the official `llama-server` build, download GGUF models, run the server.

Downloads use official upstream release assets (ggml-org/llama.cpp) and Hugging Face file URLs, are resumable, and
GGUF files are verified against the SHA-256 Hugging Face publishes for the LFS object.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HOME = ROOT / "models"
GITHUB_API = "https://api.github.com/repos/ggml-org/llama.cpp"


def _gpu_available() -> bool:
    return shutil.which("nvidia-smi") is not None


def pick_assets(asset_names: list[str], tag: str, system: str | None = None, cuda: bool | None = None) -> list[str]:
    """Choose the release assets for this machine: CUDA build (+ cudart runtime DLLs on Windows), else Vulkan/CPU."""
    system = system or platform.system()
    cuda = _gpu_available() if cuda is None else cuda
    names = set(asset_names)
    if system == "Windows":
        if cuda:
            cuda_builds = sorted(
                (n for n in names if re.fullmatch(rf"llama-{tag}-bin-win-cuda-[\d.]+-x64\.zip", n)),
                key=lambda n: [int(x) for x in re.search(r"cuda-([\d.]+)-x64", n).group(1).split(".")],  # type: ignore[union-attr]
                reverse=True,
            )
            if cuda_builds:
                ver = re.search(r"cuda-([\d.]+)-x64", cuda_builds[0]).group(1)  # type: ignore[union-attr]
                runtime = f"cudart-llama-bin-win-cuda-{ver}-x64.zip"
                return [cuda_builds[0]] + ([runtime] if runtime in names else [])
        for pattern in (f"llama-{tag}-bin-win-vulkan-x64.zip", f"llama-{tag}-bin-win-cpu-x64.zip"):
            if pattern in names:
                return [pattern]
    elif system == "Linux":
        for pattern in (f"llama-{tag}-bin-ubuntu-vulkan-x64.zip", f"llama-{tag}-bin-ubuntu-x64.zip"):
            if pattern in names:
                return [pattern]
    elif system == "Darwin":
        arch = "arm64" if platform.machine() == "arm64" else "x64"
        for pattern in (f"llama-{tag}-bin-macos-{arch}.zip",):
            if pattern in names:
                return [pattern]
    raise RuntimeError(f"No suitable llama.cpp release asset found for {system} (cuda={cuda}).")


def _download(url: str, dest: Path, progress: bool = True) -> Path:
    """Resumable HTTP download."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60.0) as r:
        if r.status_code == 416:  # already complete
            part.rename(dest)
            return dest
        r.raise_for_status()
        mode = "ab" if (have and r.status_code == 206) else "wb"
        total = int(r.headers.get("content-length", 0)) + (have if mode == "ab" else 0)
        done = have if mode == "ab" else 0
        last = time.time()
        with part.open(mode) as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress and time.time() - last > 5:
                    print(f"  {dest.name}: {done / 1e6:.0f}/{total / 1e6:.0f} MB", flush=True)
                    last = time.time()
    part.rename(dest)
    return dest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class LlamaCpp:
    home: Path = DEFAULT_HOME

    @property
    def bin_dir(self) -> Path:
        return self.home / "llama.cpp"

    @property
    def server_exe(self) -> Path | None:
        name = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
        for p in self.bin_dir.rglob(name):
            return p
        found = shutil.which("llama-server")
        return Path(found) if found else None

    def installed_tag(self) -> str | None:
        marker = self.bin_dir / "VERSION"
        return marker.read_text().strip() if marker.exists() else None

    def install(self, tag: str | None = None) -> Path:
        """Download and unpack the latest official build matching this machine."""
        client = httpx.Client(timeout=30.0, follow_redirects=True)
        if tag:
            rel = client.get(f"{GITHUB_API}/releases/tags/{tag}").json()
        else:  # `releases/latest` can point at a non-build tag; the newest release in the list is the newest build
            rel = client.get(f"{GITHUB_API}/releases?per_page=1").json()[0]
        tag = str(rel["tag_name"])
        assets = {a["name"]: a["browser_download_url"] for a in rel["assets"]}
        chosen = pick_assets(list(assets), tag)
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        for name in chosen:
            print(f"downloading {name} ...", flush=True)
            zpath = _download(assets[name], self.home / "downloads" / name)
            with zipfile.ZipFile(zpath) as z:
                z.extractall(self.bin_dir)
        (self.bin_dir / "VERSION").write_text(tag)
        exe = self.server_exe
        if exe is None:
            raise RuntimeError("llama-server not found after install")
        return exe

    def pull_model(self, repo: str, filename: str, sha256: str | None = None) -> Path:
        """Download a GGUF from Hugging Face and verify it against the LFS sha256."""
        dest = self.home / "gguf" / filename
        url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
        if not dest.exists():
            print(f"downloading {repo}/{filename} ...", flush=True)
            _download(url, dest)
        expected = sha256 or self._expected_sha(url)
        if expected:
            actual = sha256_file(dest)
            if actual != expected:
                dest.unlink()
                raise RuntimeError(f"checksum mismatch for {filename}: {actual} != {expected}")
        return dest

    @staticmethod
    def _expected_sha(url: str) -> str | None:
        r = httpx.head(url, follow_redirects=False, timeout=30.0)
        etag = r.headers.get("x-linked-etag", "").strip('"')
        return etag if re.fullmatch(r"[0-9a-f]{64}", etag) else None

    def models(self) -> list[Path]:
        return sorted((self.home / "gguf").glob("*.gguf")) if (self.home / "gguf").exists() else []


@dataclass
class ServerConfig:
    model_path: Path
    port: int = 8189
    host: str = "127.0.0.1"
    ctx: int = 16384
    gpu_layers: int = 99
    parallel: int = 1
    kv_type: str = "q8_0"  # quantized KV cache halves its VRAM footprint
    flash_attn: bool = True
    cache_reuse: int = 256  # reuse KV chunks that match the previous prompt (prefix caching)
    threads: int | None = None
    extra: tuple[str, ...] = ()

    def argv(self, exe: Path) -> list[str]:
        a = [str(exe), "-m", str(self.model_path), "--host", self.host, "--port", str(self.port),
             "-c", str(self.ctx), "-ngl", str(self.gpu_layers), "--parallel", str(self.parallel),
             "--cache-reuse", str(self.cache_reuse), "--jinja"]
        if self.flash_attn:
            a += ["--flash-attn", "on"]
        if self.kv_type != "f16":
            a += ["-ctk", self.kv_type, "-ctv", self.kv_type]
        if self.threads:
            a += ["-t", str(self.threads)]
        return a + list(self.extra)


class LlamaServer:
    def __init__(self, exe: Path, cfg: ServerConfig, log_path: Path | None = None) -> None:
        self.exe, self.cfg = exe, cfg
        self.log_path = log_path or (cfg.model_path.parent.parent / "llama-server.log")
        self.proc: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.cfg.host}:{self.cfg.port}"

    def start(self, wait_s: float = 180.0) -> None:
        if self.is_healthy():
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("w")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(
            self.cfg.argv(self.exe), stdout=log, stderr=subprocess.STDOUT, cwd=self.exe.parent,
            creationflags=flags,
        )
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited ({self.proc.returncode}); see {self.log_path}")
            if self.is_healthy():
                return
            time.sleep(1.0)
        self.stop()
        raise TimeoutError(f"llama-server not healthy after {wait_s:.0f}s; see {self.log_path}")

    def is_healthy(self) -> bool:
        try:
            return httpx.get(f"{self.base_url}/health", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    def props(self) -> dict:
        try:
            return httpx.get(f"{self.base_url}/props", timeout=5.0).json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return {}

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def gpu_info() -> dict:
    """Name, total/used VRAM (MiB) from nvidia-smi, or {} when no NVIDIA GPU."""
    if shutil.which("nvidia-smi") is None:
        return {}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.strip().splitlines()[0]
        name, total, used, driver = [x.strip() for x in out.split(",")]
        return {"name": name, "vram_total_mib": int(total), "vram_used_mib": int(used), "driver": driver}
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return {}


__all__ = ["LlamaCpp", "LlamaServer", "ServerConfig", "gpu_info", "os", "pick_assets", "sha256_file"]
