# SPDX-License-Identifier: AGPL-3.0-or-later
"""Host the real `siliconforge chat` TUI in a real pseudo-terminal and serve it to a browser-hosted terminal (xterm.js) over a websocket.

Nothing is scripted here except the keystrokes a driver sends. The program under the pty is the actual app, talking to a real local model and real tools.
Usage: python server.py PORT WORKSPACE REPO_ROOT [extra args for `chat`]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import websockets
from winpty import PtyProcess

PORT, WS, REPO = int(sys.argv[1]), sys.argv[2], sys.argv[3]
EXTRA = sys.argv[4:]
COLS, ROWS = 104, 32


async def handler(ws):
    env = {**os.environ, "PYTHONPATH": REPO, "SILICONFORGE_HOME": os.environ.get("SILICONFORGE_HOME", ""), "TERM": "xterm-256color",
           "PYTHONIOENCODING": "utf-8", "COLORTERM": "truecolor"}
    py = os.environ["SF_PYTHON"]
    pty = PtyProcess.spawn([py, "-m", "services.orchestrator.main", *EXTRA], cwd=WS, env=env, dimensions=(ROWS, COLS))
    loop = asyncio.get_running_loop()

    async def pump():
        while pty.isalive():
            try:
                data = await loop.run_in_executor(None, pty.read, 65536)
            except EOFError:
                break
            if data:
                await ws.send(data)

    task = asyncio.create_task(pump())
    try:
        async for msg in ws:
            m = json.loads(msg)
            if m["type"] == "key":
                pty.write(m["data"])
    except websockets.ConnectionClosed:
        pass
    finally:
        task.cancel()
        if pty.isalive():
            pty.terminate(force=True)


async def main():
    async with websockets.serve(handler, "127.0.0.1", PORT):
        print("ready", flush=True)
        await asyncio.Future()


asyncio.run(main())
