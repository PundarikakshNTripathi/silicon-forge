#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
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

INK, MUTED, GRID = "#0e1520", "#626c7b", "#c9cfd8"
BLUE, GREEN, RED, AMBER = "#0e1520", "#17775a", "#b2392b", "#f2a900"
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
        ax.annotate(f"{e['knobs'].get('SYNTH_STRATEGY', '?')}, {e['knobs'].get('FP_CORE_UTIL', '?')}% util", (e["area_um2"], e["wns_ns"]),
                    textcoords="offset points", xytext=(7, 5), fontsize=7.5, color=MUTED)
    ax.set_xlabel("Standard-cell area (µm²)")
    ax.set_ylabel("Worst slack across corners (ns)")
    ax.set_title(f"{d['module']}: 9 real layout recipes at 10 ns, worst corner", loc="left", color=INK, fontsize=10.5)
    fig.savefig(OUT / "pareto.png")
    plt.close(fig)


def corners() -> None:
    d = latest("real-axi_lite_alu-*.json")
    if not d or not d["episodes"]:
        return
    best_i = d["best_index"] if d.get("best_index") is not None else 0
    fail_i = next(i for i, e in enumerate(d["episodes"]) if i != best_i and e["wns_ns"] < 0)
    names = {"nom_ff_n40C_1v95": "fast\n-40 °C, 1.95 V", "nom_tt_025C_1v80": "typical\n25 °C, 1.80 V", "nom_ss_100C_1v60": "slow\n100 °C, 1.60 V"}
    keys = list(names)
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    w = 0.36
    for off, idx, col, label in ((-w / 2, fail_i, RED, "a recipe that fails"), (w / 2, best_i, GREEN, "the recipe that closes")):
        e = d["episodes"][idx]
        vals = [e["corner_wns"][k] for k in keys]
        bars = ax.bar([i + off for i in range(3)], vals, w, color=col, label=f"{label} ({e['knobs']['SYNTH_STRATEGY']}, {e['knobs']['FP_CORE_UTIL']}% util)")
        for b, v in zip(bars, vals, strict=True):
            ax.text(b.get_x() + b.get_width() / 2, v + (0.12 if v >= 0 else -0.45), f"{v:.1f}", ha="center", fontsize=8, color=INK)
    ax.axhline(0, color=INK, lw=1)
    ax.set_xticks(range(3), [names[k] for k in keys])
    ax.set_ylabel("Setup slack (ns)")
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    ax.set_title(f"{d['module']} at 10 ns: only the slow corner decides", loc="left", fontsize=10.5, color=INK)
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


def _short(model: str) -> str:
    return (model.replace("cli_agy:", "").replace("-Q4_K_M", "").replace("gemini-3.8-flash-medium", "Gemini 3.8 Flash")
            .replace("qwen2.5-coder-7b-instruct-q4_k_m", "Qwen2.5-Coder-7B"))


def verilogeval() -> None:
    """pass@1 with 95% interval for every committed VerilogEval result, grouped by model; subsets are marked."""
    rows = []
    for f in sorted(glob.glob(str(RES / "eval-verilogeval-*.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        key = next((k for k in d if k.startswith("pass_at_")), None)
        if not key or d["errors"]:
            continue  # a run with failed calls is not plotted as a clean result
        a = d[key]
        rows.append((_short(d["model"]), d["arm"]["arm"], a["mean"], a["ci95"][0], a["ci95"][1], a["problems"], d["selection"].get("all", False)))
    if not rows:
        return
    rows.sort(key=lambda r: (r[0], r[1], r[5]))
    fig, ax = plt.subplots(figsize=(7.2, 0.46 * len(rows) + 1.2))
    for i, (_m, _arm, mean, lo, hi, n, full) in enumerate(rows[::-1]):
        ax.plot([lo, hi], [i, i], color=BLUE, lw=5, alpha=0.28, solid_capstyle="butt")
        ax.plot([mean, mean], [i - 0.3, i + 0.3], color=BLUE, lw=2.4)
        ax.text(1.02, i, f"{mean * 100:.1f}%  [{lo * 100:.0f}, {hi * 100:.0f}]  n={n}{'' if full else ' subset'}", va="center", fontsize=8, color=INK)
    ax.set_yticks(range(len(rows)), [f"{m}, {arm}" for m, arm, *_ in rows[::-1]], fontsize=8.5)
    ax.set_xlim(0, 1)
    ax.set_xlabel("pass@1 (95% bootstrap interval over problems)")
    ax.set_title("VerilogEval v2, spec to RTL, Icarus Verilog 14", loc="left", fontsize=10.5, color=INK)
    ax.grid(axis="y", visible=False)
    fig.savefig(OUT / "verilogeval.png")
    plt.close(fig)


def ppa_search() -> None:
    """Best reward so far against flow evaluations, per search strategy: mean across seeds with the min-max band."""
    d = latest("ppa-*-full-*.json")
    if not d:
        return
    colors = {"default": MUTED, "random": AMBER, "lhs": "#8b95a3", "bandit": RED, "gpbo": GREEN}
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    for arm, v in d["arms"].items():
        runs = [r["best_curve"] for r in v["runs"]]
        width = max(len(r) for r in runs)
        if arm == "default":
            ax.axhline(runs[0][0], color=colors[arm], ls="--", lw=1.2)
            ax.text(0.99, runs[0][0], "default knobs ", color=colors[arm], va="top", ha="right", fontsize=8,
                    transform=ax.get_yaxis_transform())
            continue
        xs = list(range(1, width + 1))
        cols = [[r[min(i, len(r) - 1)] for r in runs] for i in range(width)]
        ax.fill_between(xs, [min(c) for c in cols], [max(c) for c in cols], color=colors.get(arm, BLUE), alpha=0.15, lw=0)
        ax.plot(xs, [sum(c) / len(c) for c in cols], color=colors.get(arm, BLUE), lw=2, label=f"{arm} ({len(runs)} run{'s' if len(runs) != 1 else ''})")
    valid = [v for a in d["arms"].values() for r in a["runs"] for v in r["best_curve"] if v > -10]
    lo, hi = min(valid), max(valid)
    pad = max(0.05, (hi - lo) * 0.08)
    ax.set_ylim(lo - pad, hi + pad)  # a failed first flow scores -50 and would flatten everything else
    ax.text(0.99, 0.02, "runs whose first flow failed start below the axis", transform=ax.transAxes, ha="right", fontsize=7.5, color=MUTED)
    ax.set_xlabel("Place-and-route evaluations")
    ax.set_ylabel("Best reward so far (higher is better)")
    ax.legend(frameon=False, fontsize=8.5, loc="lower right", bbox_to_anchor=(1.0, 0.07))
    ax.set_title(f"Knob search on {d['design']} (sky130A, real flow): mean and range over seeds", loc="left", fontsize=10.5, color=INK)
    fig.savefig(OUT / "ppa_search.png")
    plt.close(fig)


def pipeline_runs() -> None:
    """Which designs each local-model pipeline run verified through equivalence (filled square) and which it did not."""
    files = sorted(glob.glob(str(RES / "local-*.json")))
    if not files:
        return
    runs = []
    for f in files:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        runs.append((Path(f).stem.split("-2026")[0].replace("local-", ""), d["results"]))
    designs = sorted({r["design"] for _, rows in runs for r in rows})
    fig, ax = plt.subplots(figsize=(7.2, 0.4 * len(runs) + 1.2))
    for yi, (_tag, rows) in enumerate(runs[::-1]):
        by = {r["design"]: r for r in rows}
        for xi, name in enumerate(designs):
            ok = bool(by.get(name) and by[name]["verified"])
            ax.scatter(xi, yi, s=170, marker="s", color=BLUE if ok else "white", edgecolor=BLUE, linewidth=1.6)
    ax.set_xticks(range(len(designs)), designs, fontsize=8.5)
    ax.set_yticks(range(len(runs)), [t for t, _ in runs[::-1]], fontsize=7.5)
    ax.set_xlim(-0.5, len(designs) - 0.5)
    ax.set_ylim(-0.6, len(runs) - 0.4)
    ax.set_title("Local-model pipeline runs: filled = verified through equivalence", loc="left", fontsize=10.5, color=INK)
    ax.grid(False)
    fig.savefig(OUT / "pipeline_runs.png")
    plt.close(fig)


if __name__ == "__main__":
    for fn in (pareto, corners, prefix_cache, telemetry, profile_cost, verilogeval, ppa_search, pipeline_runs):
        fn()
    print("plots written to", OUT)
