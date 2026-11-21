# SPDX-License-Identifier: AGPL-3.0-or-later
"""A design project: the RTL you already have, described in `siliconforge.yaml`, run through the real flow without any LLM.

```yaml
project:
  top: pio16                       # the module to implement
  sources: [rtl/*.v, gen/rtl/*.v]  # files or globs, relative to this file; order is kept, duplicates dropped
  include_dirs: [gen/rtl]          # `include search path
  defines: {SYNTHESIS: 1}          # preprocessor defines
  clock: {port: clk, period_ns: 20}
  sdc: constraints/top.sdc         # optional; without one the flow constrains `clock` only
  pdk: sky130A
  vars: {FP_CORE_UTIL: 40}         # extra LibreLane variables, checked against ALLOWED_VARS below
  waive: [antenna]                 # signoff checks you accept as failing; they are shown as waived, not hidden
```

`validate` returns every problem it can find without running a tool, so a bad project fails in a second, not after a
place-and-route run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from services.orchestrator.workspace import MANIFEST

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
#: LibreLane variables a project may set directly. Everything else is rejected so a typo cannot silently do nothing.
ALLOWED_VARS = {
    "FP_CORE_UTIL", "FP_ASPECT_RATIO", "FP_SIZING", "DIE_AREA", "CORE_AREA", "PL_TARGET_DENSITY_PCT", "PL_RESIZER_TIMING_OPTIMIZATIONS",
    "GRT_ADJUSTMENT", "GRT_REPAIR_ANTENNAS", "RUN_ANTENNA_REPAIR", "SYNTH_STRATEGY", "SYNTH_BUFFERING", "SYNTH_SIZING",
    "CTS_CLK_MAX_WIRE_LENGTH", "CTS_SINK_CLUSTERING_SIZE", "CLOCK_UNCERTAINTY_CONSTRAINT", "IO_DELAY_CONSTRAINT",
    "PDN_VPITCH", "PDN_HPITCH", "RT_MAX_LAYER", "DESIGN_REPAIR_BUFFER_INPUT_PORTS", "RUN_LINTER", "ERROR_ON_LINTER_WARNINGS",
    "MAX_FANOUT_CONSTRAINT", "TARGET_PLACEMENT_DENSITY", "RUN_CTS", "RUN_IRDROP_REPORT", "EXTRA_LEFS", "EXTRA_LIBS", "EXTRA_GDS_FILES",
    "MACROS", "PNR_SDC_FILE", "SIGNOFF_SDC_FILE", "FP_PIN_ORDER_CFG", "IO_PIN_ORDER_CFG",
}
HDL_SUFFIXES = {".v", ".sv", ".vh", ".svh"}
MODULE_RE = re.compile(r"^\s*(?:module|macromodule)\s+([A-Za-z_][A-Za-z0-9_$]*)", re.M)


class ProjectError(ValueError):
    pass


@dataclass
class Project:
    root: Path
    top: str
    sources: list[Path]
    include_dirs: list[Path] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    clock_port: str = "clk"
    period_ns: float = 10.0
    sdc: Path | None = None
    pdk: str = "sky130A"
    vars: dict[str, Any] = field(default_factory=dict)
    waive: list[str] = field(default_factory=list)  # signoff check ids the project accepts; shown as waived in the report
    name: str = ""

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, path: str | Path = ".") -> Project:
        base = Path(path).expanduser().resolve()
        manifest = base if base.is_file() else base / MANIFEST
        if not manifest.is_file():
            raise ProjectError(f"no {MANIFEST} in {base}; create one with `siliconforge flow init`")
        try:
            raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ProjectError(f"{manifest}: invalid YAML: {exc}") from exc
        proj = raw.get("project") if isinstance(raw, dict) else None
        if not isinstance(proj, dict):
            raise ProjectError(f"{manifest} has no `project:` section; `siliconforge flow init` writes one")
        root = manifest.parent
        patterns = proj.get("sources") or []
        if isinstance(patterns, str):
            patterns = [patterns]
        sources: list[Path] = []
        for pat in patterns:
            hits = sorted(p for p in root.glob(str(pat)) if p.is_file()) if any(c in str(pat) for c in "*?[") else [root / str(pat)]
            for h in hits:
                h = h.resolve()
                if h not in sources:
                    sources.append(h)
        clock = proj.get("clock") or {}
        return cls(
            root=root,
            top=str(proj.get("top") or ""),
            sources=sources,
            include_dirs=[(root / str(d)).resolve() for d in (proj.get("include_dirs") or [])],
            defines={str(k): str(v) for k, v in (proj.get("defines") or {}).items()},
            clock_port=str(clock.get("port", "clk")),
            period_ns=float(clock.get("period_ns", 10.0)),
            sdc=(root / str(proj["sdc"])).resolve() if proj.get("sdc") else None,
            pdk=str(proj.get("pdk") or raw.get("pdk") or "sky130A"),
            vars=dict(proj.get("vars") or {}),
            waive=[str(w) for w in (proj.get("waive") or [])],
            name=str(raw.get("name") or root.name),
        )

    # --------------------------------------------------------------- validation

    def validate(self) -> list[str]:
        """Every problem found without running a tool."""
        errs: list[str] = []
        if not self.top:
            errs.append("project.top is not set")
        elif not IDENT.match(self.top):
            errs.append(f"project.top '{self.top}' is not a valid module name")
        if not self.sources:
            errs.append("project.sources matched no files")
        for s in self.sources:
            if not s.is_file():
                errs.append(f"source not found: {self._rel(s)}")
            elif s.suffix.lower() not in HDL_SUFFIXES:
                errs.append(f"{self._rel(s)}: not a Verilog/SystemVerilog file")
        for d in self.include_dirs:
            if not d.is_dir():
                errs.append(f"include directory not found: {self._rel(d)}")
        if self.sdc is not None and not self.sdc.is_file():
            errs.append(f"sdc file not found: {self._rel(self.sdc)}")
        if self.period_ns <= 0:
            errs.append("clock.period_ns must be positive")
        if not IDENT.match(self.clock_port):
            errs.append(f"clock.port '{self.clock_port}' is not a valid identifier")
        for var in self.vars:
            if var not in ALLOWED_VARS:
                errs.append(f"vars.{var} is not an allowed LibreLane variable (see ALLOWED_VARS in project.py)")
        if errs:
            return errs
        modules = self.modules()
        if self.top not in modules:
            errs.append(f"top module '{self.top}' is not defined in any source (found: {', '.join(sorted(modules)) or 'none'})")
        else:
            header = self._header(self.top)
            if not re.search(rf"\b{re.escape(self.clock_port)}\b", header):
                errs.append(f"clock port '{self.clock_port}' does not appear in the port list of '{self.top}'")
        return errs

    def modules(self) -> dict[str, Path]:
        """Module name -> the file defining it, from a lexical scan (comments removed)."""
        found: dict[str, Path] = {}
        for s in self.sources:
            if s.suffix.lower() in (".vh", ".svh") or not s.is_file():
                continue
            for m in MODULE_RE.finditer(_strip_comments(s.read_text(encoding="utf-8", errors="replace"))):
                found.setdefault(m.group(1), s)
        return found

    def _header(self, module: str) -> str:
        path = self.modules()[module]
        text = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        m = re.search(rf"\bmodule\s+{re.escape(module)}\b(.*?);", text, re.S)
        return m.group(1) if m else ""

    def _rel(self, p: Path) -> str:
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return str(p)

    # ------------------------------------------------------------------ outputs

    def hdl_sources(self) -> list[Path]:
        """Files the tools read directly (headers are found through the include path)."""
        return [s for s in self.sources if s.suffix.lower() in (".v", ".sv")]

    def include_path(self) -> list[Path]:
        dirs = list(self.include_dirs)
        for s in self.sources:
            if s.parent not in dirs:
                dirs.append(s.parent)  # a header next to its user is found without being listed
        return dirs

    def fingerprint(self) -> str:
        """Hash of everything that decides the result: the sources' bytes, the settings, the constraints."""
        import hashlib
        import json

        h = hashlib.sha256()
        for s in self.sources:
            h.update(self._rel(s).encode())
            h.update(s.read_bytes())
        if self.sdc and self.sdc.is_file():
            h.update(self.sdc.read_bytes())
        h.update(json.dumps({"top": self.top, "defines": self.defines, "clock": [self.clock_port, self.period_ns],
                             "pdk": self.pdk, "vars": self.vars}, sort_keys=True, default=str).encode())
        return h.hexdigest()


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def init_project(path: str | Path, top: str, sources: list[str], clock_port: str = "clk", period_ns: float = 10.0,
                 pdk: str = "sky130A") -> Path:
    """Add a `project:` section to the manifest (creating it if needed) without disturbing the other keys."""
    base = Path(path).expanduser().resolve()
    manifest = base / MANIFEST
    raw: dict[str, Any] = {}
    if manifest.is_file():
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    if "project" in raw:
        raise ProjectError(f"{manifest} already has a `project:` section")
    raw.setdefault("name", base.name)
    raw["project"] = {"top": top, "sources": sources, "clock": {"port": clock_port, "period_ns": period_ns}, "pdk": pdk}
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return manifest
