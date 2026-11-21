# SPDX-License-Identifier: AGPL-3.0-or-later
"""LibreLane (OpenLane 2 successor) driver: config generation, execution via a Runner, metrics.json parsing."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from services.orchestrator.eda.errors import ToolRunError
from services.orchestrator.eda.runner import Runner
from services.orchestrator.state import DesignContract, PPAMetrics

# Default AppImage launcher inside WSL (extracted LibreLane devshell AppImage)
DEFAULT_WSL_COMMAND = ["$HOME/eda/librelane/squashfs-root/AppRun", "librelane"]

# Our RL "knob" names -> LibreLane configuration variables
KNOB_TO_VAR = {
    "SYNTH_STRATEGY": "SYNTH_STRATEGY",
    "FP_CORE_UTIL": "FP_CORE_UTIL",
    "PL_TARGET_DENSITY_PCT": "PL_TARGET_DENSITY_PCT",
    "GRT_ADJUSTMENT": "GRT_ADJUSTMENT",
    "CTS_CLK_MAX_WIRE_LENGTH": "CTS_CLK_MAX_WIRE_LENGTH",
    "CLOCK_PERIOD": "CLOCK_PERIOD",
}

# metrics.json keys (LibreLane/OpenLane 2 naming)
M_WNS = "timing__setup__ws"
M_TNS = "timing__setup__tns"
M_AREA = "design__instance__area"
M_AREA_STDCELL = "design__instance__area__stdcell"
M_COUNT_STDCELL = "design__instance__count__stdcell"
M_COUNT = "design__instance__count"
M_POWER = "power__total"
M_DRC = ("route__drc_errors", "magic__drc_error__count", "klayout__drc_error__count")
M_LVS = "design__lvs_error__count"


class LibreLaneFlow:
    def __init__(
        self,
        runner: Runner,
        command: list[str] | None = None,
        timing_corner: str = "worst",
        keep_steps: bool = False,
    ) -> None:
        self.timing_corner = timing_corner
        self.keep_steps = keep_steps
        self.runner = runner
        self.command = command or (DEFAULT_WSL_COMMAND if runner.name == "wsl" else ["librelane"])
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            res = self.runner.run([*self.command, "--version"], Path.cwd(), timeout=180, isolate=True)
            self._available = res.ok
        return self._available

    def build_config(
        self, contract: DesignContract, rtl_filename: str, knobs: dict[str, Any], pdk: str
    ) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "DESIGN_NAME": contract.module_name,
            "VERILOG_FILES": f"dir::{rtl_filename}",
            "CLOCK_PORT": contract.clock_port,
            "CLOCK_PERIOD": float(knobs.get("CLOCK_PERIOD", contract.target_clock_ns)),
            "PDK": pdk,
        }
        for knob, var in KNOB_TO_VAR.items():
            if knob in knobs and knob != "CLOCK_PERIOD":
                cfg[var] = knobs[knob]
        return cfg

    def run(
        self,
        pnr_dir: Path,
        contract: DesignContract,
        rtl_source: str,
        knobs: dict[str, Any],
        pdk: str,
        timeout: float = 3600.0,
    ) -> PPAMetrics:
        rtl_name = f"{contract.module_name}.sv"
        (pnr_dir / rtl_name).write_text(rtl_source, encoding="utf-8")
        cfg = self.build_config(contract, rtl_name, knobs, pdk)
        return self.run_config(pnr_dir, cfg, knobs, timeout)[0]

    def run_config(
        self, pnr_dir: Path, cfg: dict[str, Any], knobs: dict[str, Any], timeout: float = 3600.0
    ) -> tuple[PPAMetrics, dict[str, Any]]:
        """Run LibreLane on a prepared directory with a complete config. Returns the mapped PPA and the raw metrics, so
        callers that need more than PPA (the project flow's signoff gates) read the same file the flow produced."""
        t0 = time.perf_counter()
        (pnr_dir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        work = self.runner.push(pnr_dir)
        res = self.runner.run(
            [*self.command, "--run-tag", "sf", "--overwrite", "config.json"], work, timeout=timeout, isolate=True
        )
        (pnr_dir / "librelane.log").write_text(res.log, encoding="utf-8")
        self.runner.pull(work, pnr_dir)
        self.runner.cleanup(work, pnr_dir)

        run_root = pnr_dir / "runs" / "sf"
        metrics_file = run_root / "final" / "metrics.json"
        if not metrics_file.exists():
            tail = "\n".join(res.log.splitlines()[-30:])
            raise ToolRunError(f"LibreLane produced no final/metrics.json (rc={res.returncode}):\n{tail}")
        metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
        ppa = self.parse_metrics(metrics, knobs, self.timing_corner)
        if not self.keep_steps:
            self._prune_steps(run_root)
        ppa.runtime_sec = round(time.perf_counter() - t0, 2)
        ppa.run_dir = str(pnr_dir)
        gds = next((run_root / "final" / "gds").glob("*.gds"), None) if (run_root / "final" / "gds").exists() else None
        ppa.gds_path = str(gds) if gds else None
        render = next((run_root / "final" / "render").glob("*.png"), None) if (run_root / "final" / "render").exists() else None
        if render:
            ppa.extra["render_png"] = str(render)
        return ppa, metrics

    @staticmethod
    def _prune_steps(run_root: Path) -> None:
        """Delete intermediate numbered step directories (keep final/ and logs) to bound disk usage."""
        import re
        import shutil

        for child in run_root.iterdir():
            if child.is_dir() and re.match(r"^\d{2}-", child.name):
                shutil.rmtree(child, ignore_errors=True)

    @staticmethod
    def parse_metrics(
        metrics: dict[str, Any], knobs: dict[str, Any], timing_corner: str = "worst"
    ) -> PPAMetrics:
        """Map LibreLane metrics.json to PPAMetrics.

        `timing_corner`: "worst" (default, multi-corner signoff) or a substring such as "tt" / "ss" / "ff"
        selecting the worst slack among matching corners.
        """

        def num(key: str, default: float = 0.0) -> float:
            v = metrics.get(key, default)
            return float(v) if isinstance(v, (int, float)) else default

        prefix = "timing__setup__ws__corner:"
        corner_wns = {k[len(prefix):]: float(v) for k, v in metrics.items() if k.startswith(prefix)}
        if timing_corner != "worst" and any(timing_corner in c for c in corner_wns):
            wns = min(v for c, v in corner_wns.items() if timing_corner in c)
        else:
            wns = num(M_WNS)
        tns_prefix = "timing__setup__tns__corner:"
        tns_by_corner = {k[len(tns_prefix):]: float(v) for k, v in metrics.items() if k.startswith(tns_prefix)}
        if timing_corner != "worst" and any(timing_corner in c for c in tns_by_corner):
            tns = min(v for c, v in tns_by_corner.items() if timing_corner in c)
        else:
            tns = num(M_TNS)

        drc = int(max((num(k) for k in M_DRC if k in metrics), default=0.0))
        return PPAMetrics(
            wns_ns=wns,
            tns_ns=tns,
            # standard-cell area (excludes fill/tap); die area is reported in `extra`
            area_um2=num(M_AREA_STDCELL, num(M_AREA)),
            total_power_mw=num(M_POWER) * 1000.0,  # W -> mW
            cell_count=int(num(M_COUNT_STDCELL, num(M_COUNT))),
            drc_violations=drc,
            lvs_clean=int(num(M_LVS)) == 0,
            knobs=dict(knobs),
            corner_wns=corner_wns,
            extra={
                "die_area_um2": num("design__die__area"),
                "instance_area_um2": num(M_AREA),
                "hold_ws_ns": num("timing__hold__ws"),
                "timing_corner": timing_corner,
            },
        )

