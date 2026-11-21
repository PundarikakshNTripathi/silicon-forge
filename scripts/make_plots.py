#!/usr/bin/env python3
"""Render README charts from the committed result files in benchmarks/results/ (no invented numbers)."""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "results"
OUT = ROOT / "assets" / "plots"
OUT.mkdir(parents=True, exist_ok=True)

INK, MUTED, GRID = "#14202e", "#6b7d90", "#d7dee5"
BLUE, GREEN, RED, AMBER = "#2b4a7a", "#1f7a5c", "#b3402f", "#d9971a"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linestyle": ":", "figure.dpi": 160, "savefig.bbox": "tight",
})


def latest(pattern: str) -> dict | None:
    files = sorted(glob.glob(str(RES / pattern)))
    return json.loads(Path(files[-1]).read_text(encoding="utf-8")) if files else None


def pareto() -> None:
    d = latest("real-axi_lite_alu-*.json")
    if not d:
        return
    eps = d["episodes"]
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    ax.axhline(0, color=GREEN, ls="--", lw=1.4)
    ax.text(max(e["area_um2"] for e in eps), 0.08, "timing met", color=GREEN, ha="right", va="bottom", fontsize=9)
    for i, e in enumerate(eps):
        ok = e["wns_ns"] >= 0 and e["drc_violations"] == 0 and e["lvs_clean"]
        ax.scatter(e["area_um2"], e["wns_ns"], s=90 if i == d.get("best_index") else 60, color=GREEN if ok else RED,
                   edgecolor=BLUE if i == d.get("best_index") else "white", linewidth=2.2, zorder=3)
        ax.annotate(f"{e['knobs'].get('CLOCK_PERIOD', '?')} ns", (e["area_um2"], e["wns_ns"]), textcoords="offset points",
                    xytext=(6, 5), fontsize=8, color=MUTED)
    ax.set_xlabel("Standard-cell area (µm²)")
    ax.set_ylabel("Worst slack across corners (ns)")
    ax.set_title(f"{d['module']}: layout episodes on real OpenROAD (label = clock period)", loc="left", color=INK, fontsize=10.5)
    fig.savefig(OUT / "pareto.png")
    plt.close(fig)


def corners() -> None:
    d = latest("real-axi_lite_alu-*.json")
    if not d or not d["episodes"]:
        return
    e = d["episodes"][d["best_index"] if d.get("best_index") is not None else 0]
    cw = {k: v for k, v in e["corner_wns"].items() if k.startswith("nom_")}
    if not cw:
        return
    names = {"nom_ff_n40C_1v95": "fast\n-40 °C, 1.95 V", "nom_tt_025C_1v80": "typical\n25 °C, 1.80 V", "nom_ss_100C_1v60": "slow\n100 °C, 1.60 V"}
    fig, ax = plt.subplots(figsize=(5.4, 3.4))
    keys = [k for k in names if k in cw]
    ax.bar([names[k] for k in keys], [cw[k] for k in keys], color=[GREEN if cw[k] >= 0 else RED for k in keys], width=0.55)
    ax.axhline(0, color=INK, lw=1)
    ax.set_ylabel("Setup slack (ns)")
    ax.set_title(f"{d['module']} at {e['knobs'].get('CLOCK_PERIOD')} ns: timing depends on the corner", loc="left", fontsize=10.5, color=INK)
    ax.grid(axis="x", visible=False)
    fig.savefig(OUT / "corners.png")
    plt.close(fig)


def prefix_cache() -> None:
    d = latest("llamacpp-prefix-cache-*.json")
    if not d:
        return
    c1, c2 = d["calls"]
    fig, ax = plt.subplots(figsize=(5.0, 3.2))
    bars = ax.bar(["first call", "repeat call"], [c1["ttft_s"], c2["ttft_s"]], color=[MUTED, BLUE], width=0.5)
    for b, c in zip(bars, (c1, c2), strict=True):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.04, f"{c['ttft_s']:.2f} s\n{c['cached_tokens']}/{c['prompt_tokens']} tokens reused",
                ha="center", fontsize=8.5, color=INK)
    ax.set_ylabel("Time to first token (s)")
    ax.set_ylim(0, c1["ttft_s"] * 1.35)
    ax.set_title(f"llama.cpp prefix cache: {d['ttft_speedup_from_prefix_cache']}× faster first token", loc="left", fontsize=10.5, color=INK)
    ax.grid(axis="x", visible=False)
    fig.savefig(OUT / "prefix_cache.png")
    plt.close(fig)


def telemetry() -> None:
    d = latest("telemetry-*.json")
    if not d:
        return
    labels = ["parse logs", "lint RTL"]
    cpp = [d["cpp_subprocess_parse_logs"]["median_ms"], d["cpp_subprocess_lint_rtl"]["median_ms"]]
    py = [d["python_inprocess_parse_logs"]["median_ms"], d["python_inprocess_lint_rtl"]["median_ms"]]
    fig, ax = plt.subplots(figsize=(5.4, 3.2))
    x = range(2)
    ax.bar([i - 0.19 for i in x], cpp, 0.36, label="C++ engine (spawned per call)", color=BLUE)
    ax.bar([i + 0.19 for i in x], py, 0.36, label="Python (in-process, fewer rules)", color=AMBER)
    ax.set_yscale("log")
    ax.set_xticks(list(x), labels)
    ax.set_ylabel("Median latency (ms, log scale)")
    ax.legend(frameon=False, fontsize=8.5)
    ax.set_title("Measured, not assumed: process spawn dominates", loc="left", fontsize=10.5, color=INK)
    ax.grid(axis="x", visible=False)
    fig.savefig(OUT / "telemetry.png")
    plt.close(fig)


def profile_cost() -> None:
    sys.path.insert(0, str(ROOT))
    from services.orchestrator.inference.catalog import load_catalog
    from services.orchestrator.inference.estimate import estimate_profile_cost

    cat = load_catalog()
    rows = [(p, estimate_profile_cost(cat, p)["usd"]) for p in ("local", "hybrid", "cloud", "openai", "gemini")]
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    ax.barh([r[0] for r in rows][::-1], [r[1] for r in rows][::-1], color=BLUE, height=0.55)
    for i, (_, v) in enumerate(rows[::-1]):
        ax.text(v + 0.002, i, f"${v:.3f}" if v else "$0 (your GPU)", va="center", fontsize=8.5, color=INK)
    ax.set_xlabel("Estimated model cost per small design (USD)")
    ax.set_title("Cost by routing profile (estimate; prices in configs/models.yaml)", loc="left", fontsize=10.5, color=INK)
    ax.grid(axis="y", visible=False)
    fig.savefig(OUT / "profile_cost.png")
    plt.close(fig)


if __name__ == "__main__":
    for fn in (pareto, corners, prefix_cache, telemetry, profile_cost):
        fn()
    print("plots written to", OUT)
