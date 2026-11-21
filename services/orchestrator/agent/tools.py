# SPDX-License-Identifier: AGPL-3.0-or-later
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
            ToolSpec("cone_of", READ,
                     "Backward cone of influence of one signal in an RTL file: the signals that can influence it and only "
                     "the lines that matter (conditions guarding an assignment included). Use it to localise a failure.",
                     {"path": "string", "signal": "string", "module": "string, optional", "context_lines": "integer, default 1"},
                     self.cone_of),
            ToolSpec("waveform", READ,
                     "Text window of a VCD trace: values of selected signals at the change times around a moment ('.' = "
                     "unchanged). Far cheaper than reading the file. Mark the failing signal to put it in the last column.",
                     {"path": "string, a .vcd file", "time": "integer, optional (default: end of trace)",
                      "signals": "list of strings, optional", "mark": "string, optional", "before": "integer, default 6",
                      "after": "integer, default 1"}, self.waveform),
            ToolSpec("trace_diff", READ,
                     "Earliest time at which two VCD traces differ (for example RTL versus a reference model, or before and "
                     "after a fix) and which signals differ, with a window around that time.",
                     {"path_a": "string", "path_b": "string", "signals": "list of strings, optional"}, self.trace_diff),
            ToolSpec("eda", COMMAND,
                     f"Run one EDA program ({', '.join(EDA_ALLOWLIST)}) with an argument list, in the workspace.",
                     {"tool": "string", "args": "list of strings", "cwd": "string, default '.'",
                      "timeout_s": "integer, default 600"}, self.eda),
            ToolSpec("run_pipeline", COMMAND,
                     "Run SiliconForge's fixed pipeline (contract, RTL, guardrails, formal, LEC, layout) on a spec file.",
                     {"spec_path": "string", "verify_only": "boolean, default true"}, self.run_pipeline),
            ToolSpec("isa_check", READ,
                     "Assemble a program against an isa.yaml and check its pin timing: returns machine code, errors with line "
                     "numbers, proven/measured cycle gaps and `.expect` violations.",
                     {"spec_path": "string", "program_path": "string", "inputs": "object name->int, optional"},
                     self.isa_check),
            ToolSpec("isa_gen", EDIT,
                     "Generate everything derived from an isa.yaml into out_dir: Verilog decoder + header, formal decoder "
                     "check, golden-model/assembler bundle (tools/<name>_isa.py), docs and bit-field SVGs.",
                     {"spec_path": "string", "out_dir": "string"}, self.isa_gen),
            ToolSpec("sim", COMMAND,
                     "Run a cocotb test module with Icarus for one or more seeds. Paths relative to the workspace. Returns "
                     "pass/fail counts and the failure text; a failing test is a result, not an error.",
                     {"sources": "list of strings", "top": "string", "test_module": "string, no .py",
                      "test_dir": "string, default '.'", "seeds": "list of integers, default [1]",
                      "includes": "list of strings, optional", "env": "object string->string, optional"}, self.sim),
            ToolSpec("mutate", COMMAND,
                     "Mutation-test a test suite: plant bugs in the given RTL files and report the kill rate and every "
                     "surviving mutant (file, line, change). test_command must exit 0 when the tests pass.",
                     {"files": "list of strings", "test_command": "string", "top": "string, optional",
                      "max_mutants": "integer, default 40", "seed": "integer, default 1",
                      "includes": "list of include dirs, optional", "support": "list of other sources the top needs, optional"},
                     self.mutate),
            ToolSpec("tt_check", READ,
                     "Tiny Tapeout preflight: info.yaml, top-module ports, reset, outputs, simulation-only constructs.",
                     {}, self.tt_check),
            ToolSpec("tt_synth", COMMAND,
                     "Synthesize the Tiny Tapeout project against the real IHP sg13cmos5l liberty: cells, area, flip-flops, "
                     "latches, and which tile sizes it fits (post-synthesis, before layout).", {}, self.tt_synth),
            ToolSpec("tt_gls", COMMAND,
                     "Run the project's cocotb tests on the synthesized gate-level netlist with the PDK cell models.",
                     {"test_module": "string, default 'test'", "test_dir": "string, default 'test'",
                      "tb": "string, default 'test/tb.v'"}, self.tt_gls),
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

    def cone_of(self, path: str, signal: str, module: str | None = None, context_lines: int = 1) -> dict:
        from services.orchestrator.guardrails.rtl_slicer import RtlDependencySlicer

        rtl = self._path(path).read_text(encoding="utf-8", errors="replace")
        slicer = RtlDependencySlicer()
        cone = slicer.cone(rtl, signal, module)
        if not cone.found:
            return {"found": False, "note": f"'{signal}' is never assigned in {path} (an input, a parameter, or a typo)"}
        return {"found": True, "module": cone.module, "signals": sorted(cone.signals),
                "slice": self._clip(slicer.extract_coi_slice(rtl, signal, int(context_lines), module))}

    def _vcd(self, path: str):
        from services.orchestrator.eda.vcd import parse_vcd

        p = self._path(path)
        if p.suffix.lower() == ".fst":
            raise ToolError("FST traces are not read directly; convert with `eda fst2vcd` (OSS CAD Suite) first")
        if not p.is_file():
            raise ToolError(f"'{path}' is not a file")
        return parse_vcd(p.read_text(encoding="utf-8", errors="replace"))

    def waveform(self, path: str, time: int | None = None, signals: list[str] | None = None, mark: str | None = None,
                 before: int = 6, after: int = 1) -> dict:
        from services.orchestrator.debug import waveform_window

        tr = self._vcd(path)
        return {"signals": list(tr.signals)[:60], "end_time": tr.end_time,
                "window": waveform_window(tr, time, int(before), int(after), signals, mark)}

    def trace_diff(self, path_a: str, path_b: str, signals: list[str] | None = None) -> dict:
        from services.orchestrator.debug import first_divergence, waveform_window

        a, b = self._vcd(path_a), self._vcd(path_b)
        hit = first_divergence(a, b, signals)
        if hit is None:
            return {"identical": True, "compared": len([s for s in (signals or a.signals) if s in b.signals])}
        t, bad = hit
        cols = bad + [s for s in (signals or list(a.signals)) if s not in bad and s in b.signals][:6]
        return {"identical": False, "time": t, "differing_signals": bad,
                "a": waveform_window(a, t, signals=cols, mark=bad[0]), "b": waveform_window(b, t, signals=cols, mark=bad[0])}

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

    # ------------------------------------------------------------------------- hardware design tools

    def _real_runner(self):
        runner = self._get_runner()
        if getattr(runner, "name", "") == "simulated":
            raise ToolError("This needs a real EDA backend, but the workspace backend is 'simulated'. The user must set "
                            "backend to wsl, native or docker in siliconforge.yaml.")
        return runner

    def isa_check(self, spec_path: str, program_path: str, inputs: dict | None = None) -> dict:
        from services.orchestrator import isa

        try:
            spec = isa.load_spec(self._path(spec_path))
        except (isa.IsaError, OSError, KeyError) as exc:
            raise ToolError(f"isa spec error: {exc}") from exc
        try:
            prog = isa.Assembler(spec).assemble(self._path(program_path).read_text(encoding="utf-8"))
        except isa.AsmError as exc:
            return {"assembled": False, "errors": exc.errors[:20]}
        rep = isa.check_timing(prog, {k: int(v) for k, v in (inputs or {}).items()})
        width = (spec.word_bits + 3) // 4
        return {"assembled": True, "words": [f"{w:0{width}x}" for w in prog.image()], "timing_ok": rep.ok,
                "problems": rep.problems[:10], "notes": rep.notes[:8],
                "static_gaps": [{"from": g.src, "to": g.dst, "min": g.min_cycles, "max": g.max_cycles}
                                for g in rep.static[:30]],
                "trace_gaps": [{"from": s, "to": d, "cycles": c} for s, d, c in rep.trace[:30]]}

    def isa_gen(self, spec_path: str, out_dir: str) -> dict:
        from services.orchestrator import isa

        out = self._path(out_dir)
        self._check_writable(out)
        try:
            files = isa.generate_all(self._path(spec_path), out)
        except (isa.IsaError, OSError, KeyError) as exc:
            raise ToolError(f"isa spec error: {exc}") from exc
        # generated artifacts are reproducible from the spec, so they get no undo entry
        return {"written": sorted(str(f.relative_to(self.ws.root.resolve()).as_posix()) for f in files)[:40]}

    def sim(self, sources: list[str], top: str, test_module: str, test_dir: str = ".", seeds: list[int] | None = None,
            includes: list[str] | None = None, env: dict | None = None) -> dict:
        from services.orchestrator import verify

        runner = self._real_runner()
        try:
            rep = verify.run_cocotb(runner, self.ws.root, sources=list(sources), top=top, test_module=test_module,
                                    test_dir=test_dir, seeds=tuple(int(s) for s in (seeds or [1])),
                                    includes=tuple(includes or ()), env={str(k): str(v) for k, v in (env or {}).items()})
        except verify.VerifyError as exc:
            raise ToolError(str(exc)) from exc
        return {"provenance": rep["provenance"], "ok": rep["ok"], "passed": rep["passed"], "failed": rep["failed"],
                "seeds": rep["seeds"], "failure_excerpts": [e[:900] for e in rep["failure_excerpts"][:3]]}

    def mutate(self, files: list[str], test_command: str, top: str | None = None, max_mutants: int = 40,
               seed: int = 1, includes: list[str] | None = None, support: list[str] | None = None) -> dict:
        from services.orchestrator import verify

        runner = self._real_runner()
        try:
            rep = verify.mutation_test(runner, self.ws.root, list(files), test_command, top=top,
                                       max_mutants=int(max_mutants), seed=int(seed), includes=tuple(includes or ()),
                                       support=tuple(support or ()))
        except verify.VerifyError as exc:
            raise ToolError(str(exc)) from exc
        return {k: rep[k] for k in ("provenance", "score", "killed", "survived", "equivalent", "invalid", "evaluated")} | {
            "survivors": rep["survivors"][:20]}

    def tt_check(self) -> dict:
        from services.orchestrator import tt

        return tt.preflight(self.ws.root).to_dict()

    def tt_synth(self) -> dict:
        from services.orchestrator import tt, verify

        runner = self._real_runner()
        try:
            rep = tt.synthesize(runner, self.ws.root)
            info = tt.load_info(self.ws.root)
        except (verify.VerifyError, FileNotFoundError) as exc:
            raise ToolError(str(exc)) from exc
        b = tt.budget(rep["area_um2"] or 0.0, str((info.get("project") or {}).get("tiles", "")) or None)
        return {"provenance": rep["provenance"], "cells": rep["cells"], "area_um2": rep["area_um2"],
                "flops": rep["flops"], "latches": rep["latches"], "sequential_area_um2": rep["sequential_area_um2"],
                "top_cells": sorted(((n, c["count"]) for n, c in rep["by_cell"].items()), key=lambda x: -x[1])[:8],
                "warnings": rep["warnings"][:8], "budget": b.get("chosen"), "smallest_that_fits": b["smallest_that_fits"],
                "note": rep["note"]}

    def tt_gls(self, test_module: str = "test", test_dir: str = "test", tb: str = "test/tb.v") -> dict:
        from services.orchestrator import tt, verify

        runner = self._real_runner()
        try:
            rep = tt.run_gls(runner, self.ws.root, test_module=test_module, test_dir=test_dir, tb=tb)
        except verify.VerifyError as exc:
            raise ToolError(str(exc)) from exc
        return {"provenance": rep["provenance"], "ok": rep["ok"], "passed": rep["passed"], "failed": rep["failed"],
                "netlist_kind": rep["netlist_kind"], "failure_excerpts": [e[:900] for e in rep["failure_excerpts"][:3]]}

    def run_pipeline(self, spec_path: str, verify_only: bool = True) -> dict:
        if self.pipeline_factory is None:
            raise ToolError("run_pipeline is not available in this session.")
        spec = self._path(spec_path)
        if not spec.is_file():
            raise ToolError(f"spec '{spec_path}' not found")
        return self.pipeline_factory(spec.read_text(encoding="utf-8"), bool(verify_only))
