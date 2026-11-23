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


def test_no_result_file_contains_the_name_of_the_machine_user_who_ran_it():
    """A WSL error once put a (garbled) account name in a committed log line; the name of whoever runs this test is checked for."""
    import getpass

    me = getpass.getuser()
    if len(me) < 3:
        return
    offenders = [f.name for f in sorted(RESULTS.glob("*.json")) if me.lower() in f.read_text(encoding="utf-8").lower()]
    assert not offenders, f"the user name appears in: {offenders}"
