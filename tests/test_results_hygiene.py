# SPDX-License-Identifier: AGPL-3.0-or-later
"""Committed result files are public: they must not carry a user's local paths or user name."""
import re
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "benchmarks" / "results"
B = chr(92)
WINDOWS_PROFILE = re.compile("[A-Za-z]:[" + B + B + "/]+Users[" + B + B + "/]+")
UNIX_HOME = re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+/")


def test_no_result_file_contains_a_local_user_path():
    offenders = []
    for f in sorted(RESULTS.glob("*.json")):
        text = f.read_text(encoding="utf-8")
        if WINDOWS_PROFILE.search(text) or UNIX_HOME.search(text):
            offenders.append(f.name)
    assert not offenders, f"local paths in: {offenders}"
