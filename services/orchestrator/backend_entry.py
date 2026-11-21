# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entry point for the packaged backend (PyInstaller) and for the Electron dev shell: serve the API and UI."""
from __future__ import annotations

import argparse
import multiprocessing


def main() -> None:
    parser = argparse.ArgumentParser(description="SiliconForge backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    from services.orchestrator.api.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
