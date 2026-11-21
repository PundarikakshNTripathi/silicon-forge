# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where things live: read-only bundled resources vs. the writable per-user data directory.

In a source checkout both are the repository root. In the packaged desktop app the resources are inside the frozen
bundle and `SILICONFORGE_HOME` (set by the Electron shell) points at the user's data directory, so runs, the
database, caches and downloaded models never end up inside the install directory.
"""
from __future__ import annotations

import os
from pathlib import Path


def _find_resource_root() -> Path:
    """Source checkout -> the repository root. Installed wheel -> the copy bundled at build time (`services/_resources`).
    `SILICONFORGE_RESOURCES` overrides both (packagers, tests)."""
    override = os.getenv("SILICONFORGE_RESOURCES")
    if override:
        return Path(override)
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "configs").is_dir():
        return checkout
    return Path(__file__).resolve().parents[1] / "_resources"


RESOURCE_ROOT = _find_resource_root()


def data_root() -> Path:
    home = os.getenv("SILICONFORGE_HOME")
    return Path(home) if home else RESOURCE_ROOT
