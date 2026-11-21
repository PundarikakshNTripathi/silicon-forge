# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plain-text rendering of an `aggregate()` result, for `siliconforge trace summary` and benchmark logs."""
from __future__ import annotations

from typing import Any


def _f(v: Any, nd: int = 2, unit: str = "") -> str:
    return "n/a" if v is None else f"{v:.{nd}f}{unit}"


def _pct(v: Any) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def render(agg: dict[str, Any]) -> str:
    if not agg.get("runs"):
        return "no runs in the trace"
    lat, tools, model, cost, rec = agg["latency"], agg["tools"], agg["model"], agg["cost"], agg["recovery"]
    lines = [f"{agg['runs']} run(s)" + (f", {agg['solved']} of {agg['judged']} solved ({_pct(agg['success_rate'])})"
                                       if agg["judged"] else ", no verdicts attached"),
             "",
             "End-to-end latency",
             f"  p50 {_f(lat['e2e_p50_s'], 2, ' s')}   p95 {_f(lat['e2e_p95_s'], 2, ' s')}   mean {_f(lat['e2e_mean_s'], 2, ' s')}",
             f"  where the time goes: reasoning {_pct(lat['reasoning_share'])}, tools {_pct(lat['action_share'])}, "
             f"overhead {_pct(lat['overhead_share'])}   (time to first token p50 {_f(lat['ttft_p50_s'], 3, ' s')})",
             "",
             "Tools",
             f"  {tools['calls']} call(s), success rate {_pct(tools['success_rate'])}, valid arguments first try "
             f"{_pct(tools['args_valid_first_try'])}, refused by policy {tools['policy_blocks']}",
             ]
    if tools["failures_by_class"]:
        lines.append("  failures: " + ", ".join(f"{k} x{v}" for k, v in sorted(tools["failures_by_class"].items())))
    lines += ["",
              "Model",
              f"  {model['calls']} call(s), {model['steps_mean']:.1f} step(s) per run, {model['invalid_actions']} invalid action(s)",
              "",
              "Cost",
              f"  ${cost['usd_total']:.4f} total, {cost['tokens_total']} tokens; per run ${cost['usd_per_run']:.4f}, "
              f"{cost['tokens_per_run']:.0f} tokens"]
    if cost["usd_per_solved"] is not None or cost["tokens_per_solved"] is not None:
        lines.append(f"  per solved task: ${_f(cost['usd_per_solved'], 4)}, {_f(cost['tokens_per_solved'], 0)} tokens")
    lines += ["",
              "Failure recovery",
              f"  {rec['failures']} failure(s), {rec['recovered']} recovered ({_pct(rec['rate'])}); median "
              f"{_f(rec['steps_to_recover_median'], 1)} step(s) and {_f(rec['tokens_to_recover_median'], 0)} tokens to recover"]
    if rec["runs_with_failure"] and rec["solved_despite_failure"] is not None:
        lines.append(f"  {rec['runs_with_failure']} run(s) had a failure; {_pct(rec['solved_despite_failure'])} of them still solved the task")
    return "\n".join(lines)
