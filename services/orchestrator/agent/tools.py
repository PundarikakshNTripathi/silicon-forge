"""The agent's tools: a small, sandboxed set that works the same for any model.

Design rules:
  * The file tools are confined to the workspace (`Workspace.resolve`). There is no shell: `eda` takes an argv list for an
    allowlist of programs. Be clear about the limit, though: `make`, `pytest` and `python3` can run arbitrary code, so
    `eda` is a `command` that needs approval unless the user chose `auto` mode for a workspace they trust.
  * The agent can never write `siliconforge.yaml` or `.siliconforge/` (otherwise it could lift its own model policy or
    forge its AI-usage ledger) and never touches `.git/`.
  * Each tool has a permission class: `read` (always allowed), `edit` (file changes) and `command` (runs a program).
    The session maps classes to approvals by mode (ask, accept-edits, auto, plan).
  * EDA results carry the runner name (`native|wsl|docker`) as provenance. With the `simulated` backend the EDA tool
    says so instead of pretending.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.orchestrator.workspace import MANIFEST, STATE_DIR, Workspace, WorkspaceError

READ, EDIT, COMMAND = "read", "edit", "command"
EDA_ALLOWLIST = ("iverilog", "vvp", "verilator", "yosys", "sby", "eqy", "mcy", "make", "pytest", "python3", "klayout")
MAX_READ_LINES = 400
MAX_OUTPUT_CHARS = 12_000
HIDDEN_DIRS = {".git", STATE_DIR, "__pycache__", "node_modules", ".venv", "sim_build"}
PROTECTED = (MANIFEST, STATE_DIR, ".git")


class ToolError(Exception):
    """A tool refused or failed; the message goes back to the model so it can adjust."""


@dataclass
class ToolSpec:
    name: str
    permission: str
    description: str
    params: dict[str, str]  # name -> "type, meaning"
    fn: Callable[..., Any]


@dataclass
class ToolBox:
    ws: Workspace
    #: lazily built so read-only sessions never need an EDA backend
    runner_factory: Callable[[], Any] | None = None
    pipeline_factory: Callable[[str, bool], dict] | None = None
    undo_stack: list[tuple[Path, bytes | None]] = field(default_factory=list)
    _runner: Any = None

    # ------------------------------------------------------------------------------------------ catalog

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec("list_dir", READ, "List files and folders.", {"path": "string, default '.'"}, self.list_dir),
            ToolSpec("read_file", READ, f"Read a text file with line numbers (max {MAX_READ_LINES} lines per call).",
                     {"path": "string", "start_line": "integer, default 1", "end_line": "integer, optional"},
                     self.read_file),
            ToolSpec("grep", READ, "Regex search over text files.",
                     {"pattern": "string (python regex)", "path": "string, default '.'", "glob": "string, default '*'"},
                     self.grep),
            ToolSpec("write_file", EDIT, "Create or overwrite a file with the full content.",
                     {"path": "string", "content": "string"}, self.write_file),
            ToolSpec("edit_file", EDIT, "Replace one exact, unique snippet in a file.",
                     {"path": "string", "old": "string", "new": "string"}, self.edit_file),
            ToolSpec("lint_rtl", READ, "Static lint of a SystemVerilog file for synthesizability problems.",
                     {"path": "string"}, self.lint_rtl),
            ToolSpec("eda", COMMAND,
                     f"Run one EDA program ({', '.join(EDA_ALLOWLIST)}) with an argument list, in the workspace.",
                     {"tool": "string", "args": "list of strings", "cwd": "string, default '.'",
                      "timeout_s": "integer, default 600"}, self.eda),
            ToolSpec("run_pipeline", COMMAND,
                     "Run SiliconForge's fixed pipeline (contract, RTL, guardrails, formal, LEC, layout) on a spec file.",
                     {"spec_path": "string", "verify_only": "boolean, default true"}, self.run_pipeline),
        ]

    def by_name(self) -> dict[str, ToolSpec]:
        return {s.name: s for s in self.specs()}

    def describe(self) -> str:
        lines = []
        for s in self.specs():
            params = ", ".join(f"{k}: {v}" for k, v in s.params.items())
            lines.append(f"- {s.name}({params}): {s.description}")
        return "\n".join(lines)

    def call(self, name: str, args: dict[str, Any]) -> Any:
        spec = self.by_name().get(name)
        if spec is None:
            raise ToolError(f"Unknown tool '{name}'. Available: {', '.join(self.by_name())}")
        try:
            return spec.fn(**args)
        except TypeError as exc:
            raise ToolError(f"Bad arguments for {name}: {exc}") from exc
        except WorkspaceError as exc:
            raise ToolError(str(exc)) from exc

    # ----------------------------------------------------------------------------------------- helpers

    def _path(self, rel: str) -> Path:
        return self.ws.resolve(rel or ".")

    def _check_writable(self, p: Path) -> None:
        rel = p.relative_to(self.ws.root.resolve())
        if rel.parts and rel.parts[0] in PROTECTED:
            raise ToolError(f"'{rel.as_posix()}' is managed by SiliconForge and cannot be changed by the agent")

    @staticmethod
    def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
        return text if len(text) <= limit else text[: limit // 2] + "\n...[truncated]...\n" + text[-limit // 2:]

    # ------------------------------------------------------------------------------------------ read tools

    def list_dir(self, path: str = ".") -> dict:
        p = self._path(path)
        if not p.is_dir():
            raise ToolError(f"'{path}' is not a directory")
        entries = []
        for child in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
            if child.name in HIDDEN_DIRS:
                continue
            entries.append({"name": child.name + ("/" if child.is_dir() else ""),
                            "size": child.stat().st_size if child.is_file() else None})
            if len(entries) >= 200:
                break
        return {"path": path, "entries": entries}

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> dict:
        p = self._path(path)
        if not p.is_file():
            raise ToolError(f"'{path}' is not a file")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start_line))
        end = min(len(lines), int(end_line) if end_line else start + MAX_READ_LINES - 1, start + MAX_READ_LINES - 1)
        body = "\n".join(f"{i + 1}\t{lines[i]}" for i in range(start - 1, end))
        return {"path": path, "total_lines": len(lines), "shown": [start, end], "content": self._clip(body)}

    def grep(self, pattern: str, path: str = ".", glob: str = "*") -> dict:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid regex: {exc}") from exc
        base = self._path(path)
        files = [base] if base.is_file() else [f for f in base.rglob(glob) if f.is_file()]
        hits: list[str] = []
        for f in files:
            rel = f.relative_to(self.ws.root.resolve())
            if any(part in HIDDEN_DIRS for part in rel.parts):
                continue
            try:
                for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{rel.as_posix()}:{n}: {line.strip()[:200]}")
                        if len(hits) >= 100:
                            return {"matches": hits, "truncated": True}
            except (UnicodeDecodeError, OSError):
                continue
        return {"matches": hits, "truncated": False}

    def lint_rtl(self, path: str) -> dict:
        from services.orchestrator.config import load_flow_config
        from services.orchestrator.guardrails.hallucination import HallucinationGuardrail

        rtl = self._path(path).read_text(encoding="utf-8", errors="replace")
        cfg = load_flow_config()
        findings = HallucinationGuardrail(cfg.pdk_spec, cfg.telemetry_binary)._run_cpp_static_lint(rtl)
        return {"passed": not any(f.severity == "ERROR" for f in findings),
                "findings": [{"rule": f.rule_id, "severity": f.severity, "line": f.line_number, "message": f.message}
                             for f in findings]}

    # ----------------------------------------------------------------------------------------- edit tools

    def _remember(self, p: Path) -> None:
        self.undo_stack.append((p, p.read_bytes() if p.exists() else None))

    def write_file(self, path: str, content: str) -> dict:
        p = self._path(path)
        self._check_writable(p)
        self._remember(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
        return {"path": path, "bytes": len(content.encode("utf-8")), "lines": content.count("\n") + 1}

    def edit_file(self, path: str, old: str, new: str) -> dict:
        p = self._path(path)
        self._check_writable(p)
        if not p.is_file():
            raise ToolError(f"'{path}' is not a file")
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            raise ToolError(f"'old' must match exactly once in {path}; it matched {n} times")
        self._remember(p)
        p.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
        return {"path": path, "replaced": 1}

    def undo(self) -> str:
        if not self.undo_stack:
            return "nothing to undo"
        p, before = self.undo_stack.pop()
        if before is None:
            p.unlink(missing_ok=True)
            return f"removed {p.relative_to(self.ws.root.resolve()).as_posix()}"
        p.write_bytes(before)
        return f"restored {p.relative_to(self.ws.root.resolve()).as_posix()}"

    # ---------------------------------------------------------------------------------------- run tools

    def _get_runner(self):
        if self._runner is None:
            if self.runner_factory is None:
                raise ToolError("No EDA backend is configured for this session.")
            self._runner = self.runner_factory()
        return self._runner

    def eda(self, tool: str, args: list[str] | None = None, cwd: str = ".", timeout_s: int = 600) -> dict:
        if tool not in EDA_ALLOWLIST:
            raise ToolError(f"'{tool}' is not allowed. Allowed: {', '.join(EDA_ALLOWLIST)}")
        args = [str(a) for a in (args or [])]
        work = self._path(cwd)
        if not work.is_dir():
            raise ToolError(f"cwd '{cwd}' is not a directory")
        for a in args:  # an argument that is a path must stay inside the workspace
            if a.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", a) or ".." in Path(a).parts:
                raise ToolError(f"argument '{a}' points outside the workspace; use workspace-relative paths")
        runner = self._get_runner()
        if getattr(runner, "name", "") == "simulated":
            raise ToolError("The workspace backend is 'simulated', which cannot run real tools. Set backend to "
                            "wsl, native or docker in siliconforge.yaml (the user must change it).")
        res = runner.run([tool, *args], work, timeout=float(timeout_s))
        return {"provenance": runner.name, "returncode": res.returncode, "elapsed_s": res.elapsed_sec,
                "output": self._clip(res.log, MAX_OUTPUT_CHARS)}

    def run_pipeline(self, spec_path: str, verify_only: bool = True) -> dict:
        if self.pipeline_factory is None:
            raise ToolError("run_pipeline is not available in this session.")
        spec = self._path(spec_path)
        if not spec.is_file():
            raise ToolError(f"spec '{spec_path}' not found")
        return self.pipeline_factory(spec.read_text(encoding="utf-8"), bool(verify_only))
