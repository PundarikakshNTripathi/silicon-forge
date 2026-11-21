# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render a flow report as one self-contained HTML page, and compare two reports.

The HTML needs no network and no script. It follows the cockpit's "engineering sheet" look: a title block, a verdict stated
once, the signoff checklist with its limits, per-corner slack, what each stage did and with which tools.
"""
from __future__ import annotations

import html
from typing import Any

CSS = """
:root{--paper:#f5f6f8;--ink:#0e1520;--ink2:#3c4657;--rule:#c9cfd8;--pass:#17775a;--fail:#b2392b;--warn:#8a5a00;--amber:#f2a900}
@media (prefers-color-scheme:dark){:root{--paper:#0f151d;--ink:#e8edf4;--ink2:#b2bccb;--rule:#243040;--pass:#4cc59a;--fail:#f27a68;--warn:#ffc84d}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1000px;margin:0 auto;padding:28px 20px 60px}h1{font-size:22px;margin:0}h2{font-size:15px;margin:30px 0 6px;border-top:2px solid var(--ink);padding-top:8px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th{text-align:left;font-size:13px;color:var(--ink2);border-bottom:2px solid var(--ink);padding:6px 10px 6px 0}
td{padding:7px 10px 7px 0;border-bottom:1px solid var(--rule);vertical-align:top}td.n,th.n{text-align:right}code,.mono{font-family:ui-monospace,Consolas,monospace;font-size:13px}
.verdict{font-size:20px;font-weight:700;margin:14px 0 2px}.pass{color:var(--pass)}.fail{color:var(--fail)}.warn{color:var(--warn)}
.stamp{display:inline-block;border:2px solid var(--ink);padding:1px 8px;font:700 12px ui-monospace,Consolas,monospace;margin-left:8px}
.muted{color:var(--ink2)}.kv{display:grid;grid-template-columns:150px 1fr;gap:2px 14px}.kv dt{color:var(--ink2)}.kv dd{margin:0}pre{white-space:pre-wrap;font-size:12px;background:rgba(127,127,127,.12);padding:8px}
@media (max-width:640px){.kv{grid-template-columns:1fr}}
"""


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _cls(status: str) -> str:
    return {"pass": "pass", "clean": "pass", "fail": "fail", "failed": "fail"}.get(status, "warn")


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None:
        return "-"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def to_html(r: dict[str, Any]) -> str:
    p, tools = r["project"], r.get("tools", {})
    overall = r["overall"]
    words = {"clean": "Every stage that ran passed.", "failed": "At least one stage or gate failed.",
             "incomplete": "A stage could not run or a required metric was not reported. This is not a pass.",
             "nothing run": "No stage was run."}
    parts = [f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<title>{_e(p['top'])}: flow report</title><style>{CSS}</style><main>",
             f"<h1>{_e(p['name'])} / {_e(p['top'])}<span class=stamp>real tools &middot; {_e(r['runner'].upper())}</span></h1>",
             f"<div class='verdict {_cls(overall)}'>{_e(overall.capitalize())}</div><div class=muted>{_e(words.get(overall, ''))}</div>"]
    parts.append("<h2>Project</h2><dl class=kv>"
                 f"<dt>PDK</dt><dd>{_e(p['pdk'])}</dd><dt>Clock</dt><dd>{_e(p['clock']['port'])} at {_e(p['clock']['period_ns'])} ns</dd>"
                 f"<dt>Sources</dt><dd>{_e(p['sources'])} files{', with an SDC file' if p.get('sdc') else ', constrained by the clock only'}</dd>"
                 f"<dt>Fingerprint</dt><dd class=mono>{_e(p['fingerprint'][:16])}</dd>"
                 f"<dt>Config hash</dt><dd class=mono>{_e((r.get('config_sha256') or 'no layout run')[:16])}</dd>"
                 f"<dt>Created</dt><dd>{_e(r['created'])}, {_e(r['elapsed_s'])} s</dd></dl>")
    parts.append("<h2>Stages</h2><table><tr><th>Stage</th><th>Result</th><th>What happened</th><th class=n>Seconds</th></tr>")
    for s in r["stages"]:
        parts.append(f"<tr><td>{_e(s['name'])}</td><td class={_cls(s['status'])}>{_e(s['status'])}</td><td>{_e(s['summary'])}</td>"
                     f"<td class=n>{_e(s['elapsed_s'])}</td></tr>")
    parts.append("</table>")
    for s in r["stages"]:
        if s["status"] in ("fail", "error") and s.get("log_tail"):
            parts.append(f"<h2>{_e(s['name'])} log (tail)</h2><pre>{_e(s['log_tail'])}</pre>")
    v = r.get("verdict")
    if v:
        parts.append("<h2>Signoff checklist</h2><table><tr><th>Check</th><th>Result</th><th class=n>Value</th><th>Limit</th><th>Detail</th></tr>")
        for c in v["checks"]:
            st = c["status"]
            shown = {"pass": "pass", "fail": "FAIL", "not reported": "not reported"}.get(st, st)
            req = "" if not c["required"] else " <span class=muted>(required)</span>"
            parts.append(f"<tr><td>{_e(c['name'])}{req}</td><td class={_cls(st)}>{_e(shown)}</td><td class=n>{_e(_fmt(c['value'], 4))}</td>"
                         f"<td>{_e(c['limit'])}</td><td class=muted>{_e(c['detail'])}</td></tr>")
        parts.append("</table>")
    if r.get("corners"):
        parts.append("<h2>Timing by corner</h2><table><tr><th>Corner</th><th class=n>Setup slack (ns)</th><th class=n>Hold slack (ns)</th></tr>")
        for c in r["corners"]:
            parts.append(f"<tr><td class=mono>{_e(c['corner'])}</td><td class='n {'fail' if (c['setup_ns'] or 0) < 0 else ''}'>{_e(_fmt(c['setup_ns']))}</td>"
                         f"<td class='n {'fail' if (c['hold_ns'] or 0) < 0 else ''}'>{_e(_fmt(c['hold_ns']))}</td></tr>")
        parts.append("</table>")
    ppa = r.get("ppa")
    if ppa:
        ex = ppa.get("extra", {})
        parts.append("<h2>Result</h2><dl class=kv>"
                     f"<dt>Standard-cell area</dt><dd>{_e(_fmt(ppa['area_um2'], 1))} &micro;m&sup2;</dd>"
                     f"<dt>Die area</dt><dd>{_e(_fmt(ex.get('die_area_um2'), 1))} &micro;m&sup2;</dd>"
                     f"<dt>Cells</dt><dd>{_e(ppa['cell_count'])}</dd><dt>Power</dt><dd>{_e(_fmt(ppa['total_power_mw'], 4))} mW</dd>"
                     f"<dt>GDSII</dt><dd class=mono>{_e(ppa.get('gds_path') or 'not produced')}</dd></dl>")
    parts.append("<h2>Tools</h2><dl class=kv>" + "".join(f"<dt>{_e(k)}</dt><dd class=mono>{_e(v)}</dd>" for k, v in tools.items()) + "</dl>")
    parts.append("<p class=muted>Generated by SiliconForge. Results come from the tools named above; a gate that is not "
                 "reported is shown as such and is never counted as a pass.</p></main></html>")
    return "".join(parts)


def diff(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """What changed from report `a` to report `b`, as plain lines. Empty when nothing that matters changed."""
    out: list[str] = []
    if a["project"]["fingerprint"] == b["project"]["fingerprint"] and a.get("config_sha256") == b.get("config_sha256"):
        out.append("same sources, constraints and flow configuration")
    elif a["project"]["fingerprint"] != b["project"]["fingerprint"]:
        out.append("the sources, constraints or project settings changed")
    elif a.get("config_sha256") != b.get("config_sha256"):
        out.append("the flow configuration changed")
    if a["overall"] != b["overall"]:
        out.append(f"overall: {a['overall']} -> {b['overall']}")
    for k in sorted(set(a.get("tools", {})) | set(b.get("tools", {}))):
        if a.get("tools", {}).get(k) != b.get("tools", {}).get(k):
            out.append(f"tool {k}: {a.get('tools', {}).get(k)} -> {b.get('tools', {}).get(k)}")
    pa, pb = a.get("ppa"), b.get("ppa")
    if pa and pb:
        for key, label, unit in (("area_um2", "standard-cell area", "um2"), ("cell_count", "cells", ""), ("total_power_mw", "power", "mW"),
                                 ("wns_ns", "worst setup slack", "ns")):
            if pa.get(key) != pb.get(key):
                x, y = pa.get(key), pb.get(key)
                rel = f" ({(y - x) / x * 100:+.1f}%)" if isinstance(x, (int, float)) and x else ""
                out.append(f"{label}: {_fmt(x, 3)} -> {_fmt(y, 3)} {unit}{rel}".replace("  ", " "))
    ca = {c["id"]: c["status"] for c in (a.get("verdict") or {}).get("checks", [])}
    cb = {c["id"]: c["status"] for c in (b.get("verdict") or {}).get("checks", [])}
    for cid in sorted(set(ca) | set(cb)):
        if ca.get(cid) != cb.get(cid):
            out.append(f"check {cid}: {ca.get(cid, 'absent')} -> {cb.get(cid, 'absent')}")
    return out
