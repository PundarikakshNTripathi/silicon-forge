# SPDX-License-Identifier: AGPL-3.0-or-later
"""Process runners: execute EDA tools natively, inside WSL2, or in Docker.

All real backends share the same flow logic and differ only in how a command is launched and how
host paths are translated, so provenance ("native" / "wsl" / "docker") is simply the runner name.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_sec: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def log(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()


class Runner:
    """Abstract runner. Subclasses implement `_exec`, `has_tool` and path handling."""

    name = "abstract"

    def run(
        self,
        cmd: list[str],
        cwd: Path | PurePosixPath,
        timeout: float = 600.0,
        env: dict[str, str] | None = None,
        isolate: bool = False,
    ) -> RunResult:
        """Run `cmd`. With `isolate`, skip the shared tool environment (for self-contained tools like LibreLane)."""
        t0 = time.perf_counter()
        try:
            proc = self._exec(cmd, cwd, timeout, env or {}, isolate)
        except FileNotFoundError as exc:
            return RunResult(127, "", f"command not found: {exc}", round(time.perf_counter() - t0, 3))
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return RunResult(124, out, f"TIMEOUT after {timeout:.0f}s", round(time.perf_counter() - t0, 3))
        return RunResult(
            proc.returncode,
            proc.stdout or "",
            proc.stderr or "",
            round(time.perf_counter() - t0, 3),
        )

    def _exec(
        self,
        cmd: list[str],
        cwd: Path | PurePosixPath,
        timeout: float,
        env: dict[str, str],
        isolate: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        raise NotImplementedError

    def has_tool(self, tool: str) -> bool:
        raise NotImplementedError

    # Workspace helpers: runners with a separate filesystem override these.
    def push(self, local_dir: Path) -> Path | PurePosixPath:
        """Make `local_dir` visible to the runner and return the path the runner should use."""
        return local_dir

    def pull(self, remote_dir: Path | PurePosixPath, local_dir: Path) -> None:
        """Copy results from the runner's workspace back into `local_dir`."""
        return None

    def cleanup(self, remote_dir: Path | PurePosixPath, local_dir: Path) -> None:
        """Remove a pushed workspace (no-op when the runner shares the local filesystem)."""
        return None


class NativeRunner(Runner):
    name = "native"

    def _exec(self, cmd, cwd, timeout, env, isolate=False):
        merged = {**os.environ, **env}
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=merged,
            encoding="utf-8", errors="replace", check=False,
        )

    def has_tool(self, tool: str) -> bool:
        return shutil.which(tool) is not None


class WslRunner(Runner):
    """Runs commands inside a WSL2 distro (Windows hosts), sourcing environment scripts first.

    Heavy I/O flows (place-and-route) use `push`/`pull` so they run on the distro's ext4
    filesystem instead of the slow /mnt/c 9P mount.
    """

    name = "wsl"

    def __init__(
        self,
        distro: str = "Ubuntu-24.04",
        env_scripts: list[str] | None = None,
        scratch: str = "$HOME/siliconforge-work",
    ) -> None:
        self.distro = distro
        self.env_scripts = env_scripts if env_scripts is not None else ["$HOME/eda/oss-cad-suite/environment"]
        self.scratch = scratch

    @staticmethod
    def to_wsl_path(path: Path | str) -> str:
        """Translate a Windows drive path (C: with either slash style) to /mnt/c/...; platform independent."""
        text = str(path)
        win = PureWindowsPath(text)
        if win.drive:
            rest = "/".join(win.parts[1:])
            return f"/mnt/{win.drive.rstrip(':').lower()}/{rest}".rstrip("/")
        return Path(text).resolve().as_posix()

    def _bash(self, script: str, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["wsl", "-d", self.distro, "--", "bash", "-lc", script],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
            check=False,
        )

    def _prelude(self) -> str:
        return "; ".join(f"source {s} >/dev/null 2>&1" for s in self.env_scripts) or ":"

    @staticmethod
    def _q(path: str) -> str:
        """Quote one argument for the WSL shell, keeping a leading $HOME expandable.

        `wsl.exe` hands the script to a shell layer that still expands `$VAR`, `$(cmd)` and backticks inside single
        quotes, so an argument like `$(rm -rf x)` would run. A backslash before `$` or a backtick survives that layer
        and leaves the character literal for the inner shell. (Backslashes and double quotes already pass through intact.)
        """
        if path.startswith("$HOME/"):
            return '"$HOME"/' + WslRunner._q(path[len("$HOME/"):])
        return shlex.quote(path.replace("$", "\\$").replace("`", "\\`"))

    def _exec(self, cmd, cwd, timeout, env, isolate=False):
        if isinstance(cwd, PurePosixPath):
            wsl_cwd = cwd.as_posix()
        else:
            wsl_cwd = self.to_wsl_path(cwd)
        exports = "".join(f"export {k}={shlex.quote(v)}; " for k, v in env.items())
        joined = " ".join(self._q(c) for c in cmd)
        prelude = ":" if isolate else self._prelude()
        script = f"{prelude}; {exports}cd {self._q(wsl_cwd)} && {joined}"
        return self._bash(script, timeout)

    def has_tool(self, tool: str) -> bool:
        res = self._bash(f"{self._prelude()}; command -v {self._q(tool)}", 90)
        return res.returncode == 0 and bool(res.stdout.strip())

    def push(self, local_dir: Path) -> PurePosixPath:
        src = self.to_wsl_path(local_dir)
        dest = f"{self.scratch}/{local_dir.name}"
        res = self._bash(
            f"mkdir -p {self._q(dest)} && cp -a {self._q(src)}/. {self._q(dest)}/ && cd {self._q(dest)} && pwd",
            300,
        )
        out = res.stdout.strip().splitlines()
        return PurePosixPath(out[-1] if out else dest)

    def pull(self, remote_dir: Path | PurePosixPath, local_dir: Path) -> None:
        dst = self.to_wsl_path(local_dir)
        self._bash(
            f"mkdir -p {self._q(dst)} && cp -a {self._q(remote_dir.as_posix())}/. {self._q(dst)}/",
            600,
        )


    def cleanup(self, remote_dir: Path | PurePosixPath, local_dir: Path) -> None:
        if str(remote_dir) != str(local_dir):
            self._bash(f"rm -rf {self._q(PurePosixPath(remote_dir).as_posix())}", 120)


class DockerRunner(Runner):
    """Runs each command in a throwaway container with the run directory mounted at /work."""

    name = "docker"

    def __init__(self, image: str = "siliconforge-eda:latest") -> None:
        self.image = image

    def _exec(self, cmd, cwd, timeout, env, isolate=False):
        envs = [x for k, v in env.items() for x in ("-e", f"{k}={v}")]
        full = ["docker", "run", "--rm", "-v", f"{cwd}:/work", "-w", "/work", *envs, self.image, *cmd]
        return subprocess.run(
            full, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", check=False,
        )

    def has_tool(self, tool: str) -> bool:
        if shutil.which("docker") is None:
            return False
        return self.run(["which", tool], Path.cwd(), timeout=120).ok
