# SPDX-License-Identifier: AGPL-3.0-or-later
"""Charts for AgentBench-SF result files (`benchmarks/results/agentbench-*.json`): who solved what, where the time and tokens go,
and how each arm handles tool failures. Every number comes from the result file; nothing is typed in.

Arms are coloured with three hues of the IC-layer palette (metal-1 cobalt, poly vermilion, diffusion green) and also told apart by
marker shape and direct labels, so the charts survive greyscale and colour-blind viewing. A `+guards` variant of an arm uses the
same hue, hollow.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import to_rgb  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "results"
OUT = ROOT / "assets" / "plots"

INK, MUTED, GRID, PAPER = "#0e1520", "#626c7b", "#d5d9e0", "#f7f5f0"
ARM_COLOR = {"custom": "#1f6feb", "custom_prev": "#7a8aa3", "langgraph": "#d9482b", "pydantic_ai": "#0f9d74"}
ARM_MARKER = {"custom": "o", "custom_prev": "D", "langgraph": "s", "pydantic_ai": "^"}
ARM_LABEL = {"custom": "SiliconForge loop", "custom_prev": "SiliconForge loop (v0.6.0)", "langgraph": "LangGraph", "pydantic_ai": "Pydantic AI"}
CATEGORY_ORDER = ["comprehension", "editing", "recovery", "judgement", "safety"]

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID,
    "grid.linestyle": ":", "figure.dpi": 160, "savefig.bbox": "tight", "figure.facecolor": "white",
})


def base(arm: str) -> str:
    return arm.split("+")[0]


def label(arm: str) -> str:
    return ARM_LABEL.get(base(arm), arm) + (" + our guards" if arm.endswith("+guards") else "")


def hollow(arm: str) -> bool:
    return arm.endswith("+guards")


def load(pattern: str) -> dict | None:
    files = sorted(glob.glob(str(RES / pattern)))
    return json.loads(Path(files[-1]).read_text(encoding="utf-8")) if files else None


def _title(doc: dict) -> str:
    return f"{doc['model']}, {doc['trials']} trials per task, {len(doc['tasks'])} tasks"


def success(doc: dict, name: str = "agentbench_success.png") -> None:
    arms = doc["summary"]["arms"]
    fig, ax = plt.subplots(figsize=(7.4, 0.62 * len(arms) + 1.5))
    for i, (arm, a) in enumerate(reversed(list(arms.items()))):
        ts, co = a["task_success"], a.get("task_content_ok") or a["task_success"]
        col = ARM_COLOR.get(base(arm), MUTED)
        ax.plot([ts["ci95"][0], ts["ci95"][1]], [i, i], color=col, lw=6, alpha=0.25, solid_capstyle="butt")
        ax.scatter(ts["mean"], i, s=90, marker=ARM_MARKER.get(base(arm), "o"), color="white" if hollow(arm) else col,
                   edgecolor=col, linewidth=2, zorder=3, clip_on=False)
        if abs(co["mean"] - ts["mean"]) > 1e-9:
            ax.scatter(co["mean"], i + 0.28, s=36, marker="D", color="white", edgecolor=col, linewidth=1.3, zorder=3, clip_on=False)
        ax.text(1.02, i, f"{ts['mean'] * 100:.1f}%  [{ts['ci95'][0] * 100:.0f}, {ts['ci95'][1] * 100:.0f}]", va="center", fontsize=8.5,
                color=INK)
    ax.set_yticks(range(len(arms)), [label(a) for a in reversed(list(arms))], fontsize=9.5)
    ax.set_xlim(0, 1.0)
    ax.set_xlabel("Task success (95% bootstrap interval over tasks)")
    ax.set_title("Task success by agent loop", loc="left", fontsize=11, color=INK)
    ax.text(0, -0.28, f"{_title(doc)}.  Diamond: content-correct, forgiving a missing answer-format line (secondary).",
            transform=ax.transAxes, fontsize=7.5, color=MUTED)
    ax.grid(axis="y", visible=False)
    fig.savefig(OUT / name)
    plt.close(fig)


def violations(doc: dict, name: str = "agentbench_guards.png") -> None:
    """Safety violations out of runs, per arm, on the two safety tasks: what adding our redaction and fencing to a framework does.
    Counts, not intervals: with 20 runs per arm the numbers are small and the chart says so."""
    arms = doc["summary"]["arms"]
    fig, ax = plt.subplots(figsize=(7.4, 0.55 * len(arms) + 1.7))
    for i, (arm, a) in enumerate(reversed(list(arms.items()))):
        col = ARM_COLOR.get(base(arm), MUTED)
        n, v = a["runs"], a["safety_violation_runs"]
        ax.barh(i, n, color="#eef0f3", height=0.5, zorder=1)
        ax.barh(i, v, color="white" if hollow(arm) else col, edgecolor=col, linewidth=1.8, height=0.5, zorder=2)
        ax.text(n + 0.4, i, f"{v} of {n} runs", va="center", fontsize=9, color=INK)
    ax.set_yticks(range(len(arms)), [label(a) for a in reversed(list(arms))], fontsize=9.5)
    ax.set_xlim(0, max(a["runs"] for a in arms.values()) * 1.35)
    ax.set_xlabel("Runs with a safety violation (a leaked secret or an obeyed injected instruction)")
    ax.set_title("Safety violations, with and without our guards", loc="left", fontsize=11, color=INK)
    ax.text(0, -0.32, f"{doc['model']}, {doc['trials']} trials on each of the 2 safety tasks. Small numbers: a signal, not a precise rate.",
            transform=ax.transAxes, fontsize=7.5, color=MUTED)
    ax.grid(axis="y", visible=False)
    fig.savefig(OUT / name)
    plt.close(fig)


def matrix(doc: dict, name: str = "agentbench_matrix.png") -> None:
    arms = doc["summary"]["arms"]
    cat = {}
    for r in doc["runs"]:
        cat[r["task"]] = r["category"]
    tasks = sorted(cat, key=lambda t: (CATEGORY_ORDER.index(cat[t]) if cat[t] in CATEGORY_ORDER else 99, t))
    fig, ax = plt.subplots(figsize=(0.62 * len(tasks) + 2.4, 0.62 * len(arms) + 1.9))
    for yi, (arm, a) in enumerate(reversed(list(arms.items()))):
        col = to_rgb(ARM_COLOR.get(base(arm), MUTED))
        for xi, t in enumerate(tasks):
            v = a["by_task"].get(t)
            if v is None:
                ax.add_patch(plt.Rectangle((xi - 0.46, yi - 0.46), 0.92, 0.92, fill=False, ec=GRID, hatch="///", lw=0.5))
                continue
            shade = tuple(1 - (1 - c) * (0.18 + 0.82 * v) for c in col)
            ax.add_patch(plt.Rectangle((xi - 0.46, yi - 0.46), 0.92, 0.92, fc=shade, ec="white", lw=1))
            ax.text(xi, yi, f"{v * 100:.0f}", ha="center", va="center", fontsize=8.5, color="white" if v > 0.62 else INK)
    ax.set_xlim(-0.6, len(tasks) - 0.4)
    ax.set_ylim(-0.6, len(arms) - 0.4)
    ax.set_xticks(range(len(tasks)), [t.replace("_", " ") for t in tasks], rotation=40, ha="right", fontsize=8)
    ax.set_yticks(range(len(arms)), [label(a) for a in reversed(list(arms))], fontsize=9)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    start = 0
    for c in CATEGORY_ORDER:  # a thin rule and a label under each task group
        n = sum(1 for t in tasks if cat[t] == c)
        if n:
            ax.annotate(c, ((start + start + n - 1) / 2, len(arms) - 0.35), ha="center", va="bottom", fontsize=8, color=MUTED)
            if start:
                ax.axvline(start - 0.5, color=GRID, lw=1)
            start += n
    ax.set_title("Per-task success rate (percent of trials)", loc="left", fontsize=11, color=INK, pad=22)
    fig.savefig(OUT / name)
    plt.close(fig)


def profile(doc: dict, name: str = "agentbench_profile.png") -> None:
    arms = list(doc["summary"]["arms"].items())
    fig, axes = plt.subplots(1, 3, figsize=(12.4, 0.55 * len(arms) + 2.2), gridspec_kw={"width_ratios": [1, 1, 1.15]})
    ys = list(range(len(arms)))[::-1]
    names = [label(a) for a, _ in arms]
    ax = axes[0]
    for y, (arm, a) in zip(ys, arms, strict=True):
        lat = a["metrics"]["latency"]
        col = ARM_COLOR.get(base(arm), MUTED)
        ax.barh(y, lat["e2e_p50_s"], color=col, height=0.5, alpha=0.9)
        ax.plot([lat["e2e_p95_s"]] * 2, [y - 0.3, y + 0.3], color=INK, lw=1.6)
        ax.text(lat["e2e_p95_s"] + 0.5, y, f"p50 {lat['e2e_p50_s']:.1f}s, p95 {lat['e2e_p95_s']:.0f}s", va="center", fontsize=7.5, color=INK)
    ax.set_yticks(ys, names, fontsize=9)
    ax.set_xlabel("End-to-end seconds per task")
    ax.set_title("Time", loc="left", fontsize=10.5, color=INK)
    ax.set_xlim(0, max(a["metrics"]["latency"]["e2e_p95_s"] for _, a in arms) * 1.6)
    ax = axes[1]
    for y, (arm, a) in zip(ys, arms, strict=True):
        col = ARM_COLOR.get(base(arm), MUTED)
        tok = a["metrics"]["cost"]["tokens_per_run"]
        ax.barh(y, tok, color=col, height=0.5, alpha=0.9)
        ax.text(tok + 150, y, f"{tok:,.0f} per task\nfirst prompt: {a['first_prompt_tokens_median']:.0f}", va="center", fontsize=7.5,
                color=INK)
    ax.set_yticks(ys, [""] * len(ys))
    ax.set_xlabel("Model tokens per task")
    ax.set_title("Tokens", loc="left", fontsize=10.5, color=INK)
    ax.set_xlim(0, max(a["metrics"]["cost"]["tokens_per_run"] for _, a in arms) * 1.6)
    ax = axes[2]
    for y, (arm, a) in zip(ys, arms, strict=True):
        col = ARM_COLOR.get(base(arm), MUTED)
        ts, rc = a["metrics"]["tools"]["success_rate"], a["metrics"]["recovery"]["rate"]
        ax.scatter(ts * 100, y + 0.13, s=70, marker="o", color=col, zorder=3, clip_on=False)
        ax.scatter((rc if rc is not None else 0) * 100, y - 0.13, s=70, marker="D", color="white", edgecolor=col, linewidth=2, zorder=3, clip_on=False)
        ax.text(102, y + 0.13, f"{ts * 100:.0f}% of calls ok", va="center", fontsize=7.5, color=INK)
        ax.text(102, y - 0.13, "no failures" if rc is None else f"{rc * 100:.0f}% of failures recovered", va="center", fontsize=7.5, color=INK)
    ax.set_yticks(ys, [""] * len(ys))
    ax.set_xlim(40, 101)
    ax.set_xlabel("Percent")
    ax.set_title("Tools and recovery", loc="left", fontsize=10.5, color=INK)
    for ax in axes:
        ax.grid(axis="y", visible=False)
        ax.set_ylim(-0.7, len(arms) - 0.3)
    fig.suptitle(f"Where the time, tokens and failures go.  {_title(doc)}", x=0.01, ha="left", fontsize=9, color=MUTED, y=1.02)
    fig.savefig(OUT / name)
    plt.close(fig)


def latest(docs: list[tuple[str, dict]], name: str = "agentbench_latest.png") -> None:
    """The current comparison: one panel per frozen suite that was written before the change it measures, same arms in the same order,
    so the reader sees the lead on one suite and the tie on the next."""
    fig, axes = plt.subplots(len(docs), 1, figsize=(7.6, 2.7 * len(docs) + 0.6), sharex=True)
    for ax, (title, doc) in zip(axes, docs, strict=True):
        arms = doc["summary"]["arms"]
        for i, (arm, a) in enumerate(reversed(list(arms.items()))):
            ts = a["task_success"]
            col = ARM_COLOR.get(base(arm), MUTED)
            ax.plot([ts["ci95"][0], ts["ci95"][1]], [i, i], color=col, lw=6, alpha=0.25, solid_capstyle="butt")
            ax.scatter(ts["mean"], i, s=80, marker=ARM_MARKER.get(base(arm), "o"), color=col, edgecolor=col, linewidth=2, zorder=3, clip_on=False)
            ax.text(1.02, i, f"{ts['mean'] * 100:.1f}%  [{ts['ci95'][0] * 100:.0f}, {ts['ci95'][1] * 100:.0f}]", va="center", fontsize=8.5, color=INK)
        ax.set_yticks(range(len(arms)), [label(a) for a in reversed(list(arms))], fontsize=9)
        ax.set_xlim(0, 1.0)
        ax.set_title(f"{title} ({len(doc['tasks'])} tasks, {doc['trials']} trials each)", loc="left", fontsize=10, color=INK)
        ax.grid(axis="y", visible=False)
    axes[-1].set_xlabel("Task success (95% bootstrap interval over tasks); same model, tools, sandbox and budget for every loop")
    fig.savefig(OUT / name)
    plt.close(fig)


def main(pattern: str = "agentbench-*-run3-*.json") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    second, third = load("agentbench-*-heldout-ask-*.json"), load("agentbench-*-fresh-fmt-*.json")
    if second and third:
        latest([("Second suite, frozen before the ask-first change", second), ("Third suite, frozen before the answer-format fix", third)])
    doc = load(pattern)
    if doc:
        success(doc)
        matrix(doc)
        profile(doc)
    guards = load("agentbench-*-guards-*.json")
    if guards:
        violations(guards)


if __name__ == "__main__":
    main()
