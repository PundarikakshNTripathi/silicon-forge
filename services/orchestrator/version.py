# SPDX-License-Identifier: AGPL-3.0-or-later
"""The package version, with one source of truth: `pyproject.toml` in a checkout, installed metadata otherwise."""
from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path


def _version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        try:
            return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            pass
    try:
        return metadata.version("siliconforge")
    except metadata.PackageNotFoundError:
        return "0+unknown"


__version__ = _version()
