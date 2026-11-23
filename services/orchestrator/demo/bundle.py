# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the data the static demo serves: one JSON file per API read the dashboard makes, taken from the real route code.

The dashboard in demo mode (`VITE_DEMO_MODE=true`) has no backend. Every number it shows must therefore come from a committed
file or a recorded run, never from code written for the demo. So:

* the committed benchmark results are served by the real `/api/benchmarks` route;
* the committed, sanitised real runs under `benchmarks/demo/runs/` go through the real `Store` and the real run routes;
* the agent-insights panel is the real `telemetry.aggregate` over the committed AgentBench run metrics;
* the replay trace comes from `record_trace` (scripted model, real tools: see that module for what is and is not real);
* anything that describes the machine it ran on (GPU, installed tools, local model files, secrets) is replaced by neutral
  values, so the bundle is the same on every machine and leaks nothing about the one that built it.

The output is deterministic: no timestamps of its own and sorted keys, so a test can rebuild it and compare bytes.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from services.orchestrator.demo.record_trace import record
from services.orchestrator.paths import RESOURCE_ROOT
from services.orchestrator.version import __version__

DEMO_RUNS = RESOURCE_ROOT / "benchmarks" / "demo" / "runs"
DEFAULT_OUT = RESOURCE_ROOT / "services" / "dashboard" / "public" / "demo-data"
DEFAULT_ASSETS = RESOURCE_ROOT / "services" / "dashboard" / "public" / "demo-assets"

#: images and animations the demo page shows (source under the repo -> name under demo-assets/)
ASSETS = {
    "assets/gifs/tui.gif": "tui.gif", "assets/gifs/web-tour.gif": "web-tour.gif",
    "assets/screenshots/tui-chat.png": "tui-chat.png", "assets/screenshots/tui-approval.png": "tui-approval.png",
}
MAX_ASSET_BYTES = 3_500_000  # one animation may be this big; the whole folder is checked against a budget in the tests

# ---------------------------------------------------------------------------------------------- sanitising

_PATTERNS = [
    re.compile(r"[A-Za-z]:[\\/]+(?:[^\\/\"']+[\\/]+)*?siliconforge[\\/]+", re.I),  # C:\...\siliconforge\  (escaped or not)
    re.compile(r"/mnt/[a-z]/(?:[^/\"']+/)*?siliconforge/", re.I),  # /mnt/c/.../siliconforge/  (WSL)
]
_LEFTOVER = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|/mnt/[a-z]/|/home/|/Users/|AppData", re.I)


def scrub_text(text: str) -> str:
    """Drop the absolute prefix of the checkout from any path so what is left is repo-relative. The mangled `um?` in one event
    is a superscript two that a Windows console re-encoded; it is restored, nothing else is edited."""
    for pat in _PATTERNS:
        text = pat.sub("", text)
    return text.replace("um\ufffd", "um²")


def scrub(obj: Any) -> Any:
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    return obj


def leftovers(text: str) -> list[str]:
    """Anything that still looks like a local path or a user name (the tests require this to be empty)."""
    found = set(m.group(0) for m in _LEFTOVER.finditer(text))
    try:
        me = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name available (some containers); the patterns above still apply
        me = ""
    if len(me) >= 3 and me.lower() in text.lower():
        found.add("<the user name of the machine that built this>")
    return sorted(found)


def capture_run(run_dir: Path, name: str) -> Path:
    """Copy a finished real run into `benchmarks/demo/runs/<name>.json`, sanitised. Run after any flow change that you want shown."""
    report = json.loads((run_dir / "signoff_report.json").read_text(encoding="utf-8"))
    st = report["state"]
    st.pop("gds_path", None)  # a path on the machine that ran it; the layout is shown as an image instead
    doc = {"name": name, "run_id": f"demo-{name}", "report": scrub(report)}
    bad = leftovers(json.dumps(doc, ensure_ascii=False))
    if bad:
        raise ValueError(f"the sanitised run still contains local details: {bad}")
    DEMO_RUNS.mkdir(parents=True, exist_ok=True)
    dest = DEMO_RUNS / f"{name}.json"
    dest.write_text(json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------------------------- the build

def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _load_runs(store: Any, runs_root: Path, layouts: dict[str, str]) -> list[str]:
    from services.orchestrator.api.manager import RunRequest

    ids = []
    for f in sorted(DEMO_RUNS.glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        rep, rid = doc["report"], doc["run_id"]
        st = rep["state"]
        req = RunRequest(spec_text=st["spec_text"], profile="recorded", backend=rep["eda_mode"]).model_dump()
        store.create_run(rid, st["spec_text"], "recorded", rep["eda_mode"], req)
        for ev in st["events"]:
            store.add_event(rid, ev)
        store.finish_run(rid, "succeeded" if st.get("signoff_passed") else "failed", st, rep["cost"], rep["elapsed_sec"],
                         st.get("error_message"))
        first, last = st["events"][0]["ts"], st["events"][-1]["ts"]
        with sqlite3.connect(store.path) as db:  # a stored run keeps the times it really had, not the time of the export
            db.execute("UPDATE runs SET created=?, updated=? WHERE id=?", (first, last, rid))
        art = runs_root / rid
        art.mkdir(parents=True, exist_ok=True)
        (art / "signoff_report.json").write_text(json.dumps(rep, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        layout = layouts.get(st["contract"]["module_name"])
        if layout:
            shutil.copy(RESOURCE_ROOT / "assets" / "layout" / layout, art / "layout.png")
        ids.append(rid)
    return ids


def _neutral_system(catalog_providers: list[dict[str, Any]]) -> dict[str, Any]:
    """What the System, Models and New run pages read about the machine, with nothing from the machine that built the bundle."""
    doctor = {
        "platform": "static demo", "python": "n/a",
        "host_tools": {t: False for t in ("python", "node", "pnpm", "cmake", "git", "wsl", "docker", "nvidia-smi", "llama-server")},
        "gpu": {}, "backends": {"simulated": {"available": True, "tools": {}, "note": "static demo: nothing runs here"}},
        "telemetry_engine_built": False, "llama_cpp": {"installed": False, "tag": None, "models": []},
    }
    models = {"installed": False, "tag": None, "models": [], "server": {"running": False, "model": None, "props": {}}, "gpu": {},
              "discovered": [], "other_servers": [], "pulls": {}}
    providers = [{k: v for k, v in p.items() if k in ("name", "kind", "local")} for p in catalog_providers]
    return {"doctor": doctor, "models": models, "providers": providers}


def build(out: Path | None = None, assets_out: Path | None = None) -> dict[str, Any]:
    """Write the bundle and return a manifest of what it holds. `out` defaults to the dashboard's `public/demo-data/`."""
    out, assets_out = Path(out or DEFAULT_OUT), Path(assets_out or DEFAULT_ASSETS)
    from fastapi.testclient import TestClient

    from services.orchestrator.api.app import DEFAULT_SETTINGS, create_app
    from services.orchestrator.api.store import Store
    from services.orchestrator.inference.catalog import load_catalog
    from services.orchestrator.telemetry.metrics import aggregate

    with tempfile.TemporaryDirectory(prefix="sf-demo-bundle-", ignore_cleanup_errors=True) as t:  # SQLite files stay locked on Windows
        tmp = Path(t)
        prev = os.environ.get("SILICONFORGE_HOME")
        os.environ["SILICONFORGE_HOME"] = str(tmp / "home")  # no user skills, results or models can leak in
        try:
            runs_root = tmp / "runs"
            db = runs_root / "siliconforge.db"
            store = Store(db)
            run_ids = _load_runs(store, runs_root, {"up_down_counter": "up_down_counter.png", "axi_lite_alu": "axi_lite_alu.png"})
            app = create_app(db_path=db, serve_ui=False, runs_root=runs_root)
            client = TestClient(app)

            def get(path: str) -> Any:
                r = client.get(f"/api{path}")
                r.raise_for_status()
                return r.json()

            if out.exists():
                shutil.rmtree(out)
            out.mkdir(parents=True)

            runs = get("/runs?limit=100")
            _dump(out / "runs.json", runs)
            for rid in run_ids:
                _dump(out / "runs" / f"{rid}.json", get(f"/runs/{rid}"))
                _dump(out / "runs" / f"{rid}.events.json", get(f"/runs/{rid}/events/all"))
                arts = get(f"/runs/{rid}/artifacts")
                _dump(out / "runs" / f"{rid}.artifacts.json", arts)
                for a in arts:
                    dest = out / "run-files" / rid / a["path"]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(runs_root / rid / a["path"], dest)

            benchmarks = get("/benchmarks")
            _dump(out / "benchmarks.json", benchmarks)
            _dump(out / "examples.json", get("/examples"))
            _dump(out / "cost.json", get("/cost/summary"))
            _dump(out / "skills.json", get("/skills"))
            _dump(out / "config.json", get("/config/defaults"))
            _dump(out / "settings.json", {**DEFAULT_SETTINGS})
            cat = load_catalog()
            for profile in cat.profiles:
                _dump(out / "estimate" / f"{profile}.json", get(f"/estimate?profile={profile}"))

            sysinfo = _neutral_system([{"name": n, "kind": c.get("kind"), "local": bool(c.get("local")) or c.get("kind") == "replay"}
                                       for n, c in cat.providers.items()])
            profiles = [{"name": pn, "tiers": {tier: [{"provider": x.provider, "model": x.model} for x in chain]
                                               for tier, chain in tiers.items()},
                         "providers": sorted({x.provider for chain in tiers.values() for x in chain})}
                        for pn, tiers in cat.profiles.items()]
            _dump(out / "doctor.json", sysinfo["doctor"])
            _dump(out / "models.json", sysinfo["models"])
            _dump(out / "providers.json", {"providers": sysinfo["providers"], "profiles": profiles, "pricing_as_of": cat.pricing_as_of,
                                             "models": {m: p.__dict__ for m, p in cat.models.items()}})

            # Agent insights: the real aggregator over the committed head-to-head runs of our own loop (three repeats, 13 tasks).
            pooled = []
            files = sorted((RESOURCE_ROOT / "benchmarks" / "results").glob("agentbench-*-run[123]-*.json"))
            for f in files:
                pooled += [r["metrics"] for r in json.loads(f.read_text(encoding="utf-8"))["runs"]
                           if r["arm"] == "custom" and r.get("metrics")]
            _dump(out / "insights.json", {"hours": 0, "spans": len(pooled), "metrics": aggregate(pooled),
                                          "source": "AgentBench head-to-head: SiliconForge loop, " + str(len(files)) +
                                                    " repeats of 13 tasks x 5 trials (benchmarks/results/agentbench-*-run1..3)"})
        finally:
            if prev is None:
                os.environ.pop("SILICONFORGE_HOME", None)
            else:
                os.environ["SILICONFORGE_HOME"] = prev

    _dump(out / "replay.json", record())

    assets_out.mkdir(parents=True, exist_ok=True)
    for old in assets_out.glob("*"):
        old.unlink()
    for src, name in ASSETS.items():
        data = (RESOURCE_ROOT / src).read_bytes()
        if len(data) > MAX_ASSET_BYTES:
            raise ValueError(f"{src} is {len(data)} bytes; the demo budget per file is {MAX_ASSET_BYTES}")
        (assets_out / name).write_bytes(data)

    manifest = {"schema": "siliconforge.demo-bundle/1", "siliconforge_version": __version__, "runs": run_ids,
                "benchmark_files": sorted(b["file"] for b in benchmarks), "assets": sorted(ASSETS.values()),
                "note": "Generated by scripts/export_demo_bundle.py from committed results and recorded runs. Do not edit by hand."}
    _dump(out / "manifest.json", manifest)
    return manifest
