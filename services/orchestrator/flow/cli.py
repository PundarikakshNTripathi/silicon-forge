# SPDX-License-Identifier: AGPL-3.0-or-later
"""`siliconforge flow ...`: run your own RTL through the real flow and get a report you can keep."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from services.orchestrator.flow.report import diff, to_html
from services.orchestrator.flow.stages import STAGES, ProjectFlow
from services.orchestrator.project import Project, ProjectError, init_project


def add_parser(sub) -> None:
    f = sub.add_parser("flow", help="Run your own RTL through lint, synthesis with equivalence, and place and route")
    fs = f.add_subparsers(dest="flow_cmd", required=True)

    i = fs.add_parser("init", help="Add a `project:` section to siliconforge.yaml")
    i.add_argument("path", nargs="?", default=".")
    i.add_argument("--top", required=True)
    i.add_argument("--sources", nargs="+", required=True, help="files or globs, relative to the folder")
    i.add_argument("--clock", default="clk")
    i.add_argument("--period", type=float, default=10.0, help="clock period in ns")
    i.add_argument("--pdk", default="sky130A")

    c = fs.add_parser("check", help="Validate the project without running any tool")
    c.add_argument("path", nargs="?", default=".")

    r = fs.add_parser("run", help="Run the flow and write report.json and report.html")
    r.add_argument("path", nargs="?", default=".")
    r.add_argument("--stages", default=",".join(STAGES), help=f"comma-separated subset of {', '.join(STAGES)}")
    r.add_argument("--no-lec", action="store_true", help="synthesize without the equivalence check")
    r.add_argument("--backend", default="wsl", choices=["native", "wsl", "docker"])
    r.add_argument("--knob", action="append", default=[], metavar="VAR=VALUE", help="LibreLane variable for this run (repeatable)")
    r.add_argument("--waive", action="append", default=[], metavar="CHECK", help="accept a failing signoff check (repeatable)")
    r.add_argument("--timeout-pnr", type=float, default=7200.0)
    r.add_argument("--out", default=None)
    r.add_argument("--locked", action="store_true", help="refuse to run if an installed tool differs from flow.lock")

    lk = fs.add_parser("lock", help="Write flow.lock with the installed tool versions, or check against it")
    lk.add_argument("path", nargs="?", default=".")
    lk.add_argument("--backend", default="wsl", choices=["native", "wsl", "docker"])
    lk.add_argument("--check", action="store_true", help="compare with the existing flow.lock instead of writing it")

    rp = fs.add_parser("report", help="Re-render report.html from a report.json (or a run folder)")
    rp.add_argument("source")

    d = fs.add_parser("diff", help="What changed between two reports")
    d.add_argument("a")
    d.add_argument("b")

    f.set_defaults(func=cmd_flow)


def _load_report(path: str) -> dict:
    p = Path(path)
    if p.is_dir():
        p = p / "report.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("schema") != "siliconforge.flow/1":
        raise ValueError(f"{p}: not a siliconforge.flow/1 report")
    return data


def _knob(raw: str):
    key, _, val = raw.partition("=")
    try:
        return key.strip(), json.loads(val)
    except json.JSONDecodeError:
        return key.strip(), val


def cmd_flow(args: argparse.Namespace) -> int:
    cmd = args.flow_cmd
    try:
        if cmd == "init":
            path = init_project(args.path, args.top, args.sources, args.clock, args.period, args.pdk)
            print(f"wrote the project section to {path}")
            return 0
        if cmd == "report":
            data = _load_report(args.source)
            out = (Path(args.source) if Path(args.source).is_dir() else Path(args.source).parent) / "report.html"
            out.write_text(to_html(data), encoding="utf-8")
            print(f"wrote {out}")
            return 0
        if cmd == "diff":
            lines = diff(_load_report(args.a), _load_report(args.b))
            print("\n".join(lines) if lines else "no differences that matter")
            return 0
        project = Project.load(args.path)
        errs = project.validate()
        if cmd == "check":
            if errs:
                print("\n".join(f"  {e}" for e in errs), file=sys.stderr)
                return 1
            print(f"ok: top {project.top}, {len(project.sources)} source files, clock {project.clock_port} at {project.period_ns} ns, "
                  f"pdk {project.pdk}, fingerprint {project.fingerprint()[:12]}")
            return 0
        if errs:
            print("project is not valid:\n" + "\n".join(f"  {e}" for e in errs), file=sys.stderr)
            return 2
        if cmd == "lock":
            return _lock(project, args)
        return _run(project, args)
    except (ProjectError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _flow(project: Project, args: argparse.Namespace, out: Path, **kw) -> ProjectFlow:
    from services.orchestrator.config import load_flow_config
    from services.orchestrator.eda.bridge import make_backend

    runner = make_backend(args.backend, load_flow_config()).runner  # type: ignore[attr-defined]
    return ProjectFlow(project, runner, out, **kw)


def _lock(project: Project, args: argparse.Namespace) -> int:
    import tempfile

    from services.orchestrator.flow import lock as flock

    with tempfile.TemporaryDirectory(prefix="sf_lock_") as tmp:
        tools = _flow(project, args, Path(tmp)).tool_versions()
    if args.check:
        diffs = flock.mismatches(flock.read_lock(project.root), tools, project.pdk)
        print("\n".join(f"  {d}" for d in diffs) if diffs else "flow.lock holds")
        return 3 if diffs else 0
    path = flock.write_lock(project.root, flock.make_lock(tools, project.pdk, project.top))
    print(f"wrote {path}: " + ", ".join(f"{k} {v}" for k, v in tools.items()))
    return 0


def _discard_if_empty(folder: Path) -> None:
    """A run that was refused before any stage ran should not leave an empty output folder."""
    try:
        folder.rmdir()
    except OSError:
        pass  # not empty, or already gone


def _run(project: Project, args: argparse.Namespace) -> int:
    from services.orchestrator.flow import lock as flock

    stages = tuple(s.strip() for s in args.stages.split(",") if s.strip())
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) if args.out else project.root / ".siliconforge" / "flow" / f"{project.top}-{stamp}"
    flow = _flow(project, args, out, timeouts={"pnr": args.timeout_pnr}, waive=set(project.waive) | set(args.waive))
    runner = flow.runner
    if args.locked:
        try:
            diffs = flock.mismatches(flock.read_lock(project.root), flow.tool_versions(), project.pdk)
        except flock.LockError:
            _discard_if_empty(out)
            raise
        if diffs:
            _discard_if_empty(out)
            print("tools differ from flow.lock; nothing was run:\n" + "\n".join(f"  {d}" for d in diffs), file=sys.stderr)
            return 3
    knobs = dict(_knob(k) for k in args.knob)
    print(f"running {', '.join(stages)} on {project.top} with the {runner.name} backend; output in {out}", flush=True)
    report = flow.run(stages, lec=not args.no_lec, knobs=knobs)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "report.html").write_text(to_html(report), encoding="utf-8")
    for s in report["stages"]:
        print(f"  {s['name']:<6} {s['status']:<8} {s['summary']}")
    print(f"overall: {report['overall']}   report: {out / 'report.html'}")
    return 0 if report["overall"] == "clean" else 1
