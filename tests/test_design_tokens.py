# SPDX-License-Identifier: AGPL-3.0-or-later
"""The palette is one JSON file; the web CSS and the TUI constants are generated from it and must not drift, and text must be legible."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKENS = json.loads((ROOT / "configs" / "design" / "tokens.json").read_text(encoding="utf-8"))


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    chans = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_generated_files_are_current():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_tokens.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_text_pairs_meet_aa_in_both_themes():
    pairs = [("ink", "paper"), ("ink-2", "paper"), ("ink-3", "paper"), ("ink-3", "sunken"), ("accent", "paper"), ("on-accent", "accent"),
             ("pass", "pass-wash"), ("fail", "fail-wash"), ("repair-ink", "repair-wash"), ("chrome-ink", "chrome"), ("chrome-ink-2", "chrome")]
    for mode in ("light", "dark"):
        for fg, bg in pairs:
            assert contrast(TOKENS[mode][fg], TOKENS[mode][bg]) >= 4.5, (mode, fg, bg)


def test_stage_hues_are_distinct_and_visible_against_the_page():
    for mode in ("light", "dark"):
        hues = [v[mode].lower() for v in TOKENS["layers"].values()]
        assert len(set(hues)) == len(hues)
        for key, v in TOKENS["layers"].items():
            assert contrast(v[mode], TOKENS[mode]["paper"]) >= 3.0, (mode, key)  # graphical objects need 3:1


def test_amber_is_not_a_stage_hue():
    """Amber means 'sent back for repair' and nothing else, so no stage may be near it."""
    for mode in ("light", "dark"):
        for key, v in TOKENS["layers"].items():
            assert v[mode].lower() != TOKENS[mode]["repair"].lower(), (mode, key)
