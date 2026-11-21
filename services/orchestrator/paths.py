"""Where things live: read-only bundled resources vs. the writable per-user data directory.

In a source checkout both are the repository root. In the packaged desktop app the resources are inside the frozen
bundle and `SILICONFORGE_HOME` (set by the Electron shell) points at the user's data directory, so runs, the
database, caches and downloaded models never end up inside the install directory.
"""
from __future__ import annotations

import os
from pathlib import Path

RESOURCE_ROOT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    home = os.getenv("SILICONFORGE_HOME")
    return Path(home) if home else RESOURCE_ROOT
