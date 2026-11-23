#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the static demo's data, or capture a finished run into it.

    python scripts/export_demo_bundle.py                 # rebuild services/dashboard/public/demo-data and demo-assets
    python scripts/export_demo_bundle.py --check         # rebuild into a temp folder and fail if the committed bundle differs
    python scripts/export_demo_bundle.py --capture <run_dir> <name>   # sanitise a finished real run into benchmarks/demo/recorded/

The demo is a static copy of the dashboard fed by these files; see docs/demo.md. Nothing here calls a model.
"""
from __future__ import annotations

import argparse
import filecmp
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.orchestrator.demo import bundle  # noqa: E402


def _same(a: Path, b: Path) -> list[str]:
    """Files that differ between two folders (names relative to the folder)."""
    diffs: list[str] = []
    left = {p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file()}
    right = {p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file()}
    diffs += [f"only in the committed bundle: {n}" for n in sorted(left - right)]
    diffs += [f"missing from the committed bundle: {n}" for n in sorted(right - left)]
    diffs += [f"differs: {n}" for n in sorted(left & right) if not filecmp.cmp(a / n, b / n, shallow=False)]
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="rebuild into a temp folder and compare with the committed bundle")
    ap.add_argument("--capture", nargs=2, metavar=("RUN_DIR", "NAME"), help="sanitise a finished run into benchmarks/demo/recorded/")
    args = ap.parse_args()
    if args.capture:
        dest = bundle.capture_run(Path(args.capture[0]), args.capture[1])
        print(f"wrote {dest.relative_to(ROOT)}")
        return 0
    if args.check:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            bundle.build(tmp / "data", tmp / "assets")
            diffs = _same(bundle.DEFAULT_OUT, tmp / "data") + _same(bundle.DEFAULT_ASSETS, tmp / "assets")
        if diffs:
            print("The committed demo bundle is out of date. Run: python scripts/export_demo_bundle.py")
            print("\n".join(diffs[:20]))
            return 1
        print("demo bundle is up to date")
        return 0
    m = bundle.build()
    print(f"wrote demo data for {len(m['runs'])} runs and {len(m['benchmark_files'])} result files")
    note = ROOT / "services" / "dashboard" / "src" / "demo" / "engineeringNote.md"
    if "[AUTHOR TO WRITE BY HAND]" in note.read_text(encoding="utf-8"):
        print("warning: the engineering note still has unwritten sections; the demo shows them as placeholders", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
