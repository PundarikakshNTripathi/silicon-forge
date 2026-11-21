# SPDX-License-Identifier: AGPL-3.0-or-later
"""Secret lookup: environment variable first, then the OS keychain (never a config file)."""
from __future__ import annotations

import os

SERVICE = "siliconforge"


def get_secret(env_name: str | None) -> str | None:
    if not env_name:
        return None
    value = os.getenv(env_name)
    if value:
        return value
    try:
        import keyring

        return keyring.get_password(SERVICE, env_name)
    except Exception:  # noqa: BLE001 - keyring missing/unavailable must never break a run
        return None


def set_secret(env_name: str, value: str) -> bool:
    try:
        import keyring

        keyring.set_password(SERVICE, env_name, value)
        return True
    except Exception:  # noqa: BLE001
        return False


def delete_secret(env_name: str) -> bool:
    try:
        import keyring

        keyring.delete_password(SERVICE, env_name)
        return True
    except Exception:  # noqa: BLE001
        return False


def has_secret(env_name: str | None) -> bool:
    return bool(get_secret(env_name))
