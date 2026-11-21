# SPDX-License-Identifier: AGPL-3.0-or-later
"""Use external MCP servers as agent tools.

```yaml
mcp_servers:
  - name: openroad                    # tools appear as mcp__openroad__<tool>
    command: [python, -m, my_openroad_mcp]
    env: {OPENROAD_HOME: /opt/or}     # added to a minimal default environment, not to the whole of yours
    permission: command               # read | edit | command: what the tools ask for (default command)
    timeout: 120                      # seconds per call
```

Each server is started as a subprocess over stdio when the agent session starts and stopped when it ends. Its tools are added
to the agent's tool list as ordinary tools, so everything that applies to built-in tools applies to them: permission mode and
approval, allow/deny rules, hooks, the loop guard, redaction, and fencing of their output as untrusted data. Rules match the
full tool name (`mcp__openroad__run_script`) and, for arguments called `path`, a glob over the path; there is no wildcard over
tool names.

What this does not do: it does not vet a server. An MCP server is code you chose to run with your privileges. Tools default to
the `command` permission, so `ask` mode confirms every call; use `read` only for servers you know to be read-only. A server that
fails to start is reported and its tools are absent; it is never replaced by a silent substitute.
"""
from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.orchestrator.agent.tools import COMMAND, EDIT, READ, ToolError, ToolSpec

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
PERMISSIONS = {"read": READ, "edit": EDIT, "command": COMMAND}
MAX_TOOLS_PER_SERVER = 64


class McpConfigError(ValueError):
    pass


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)
    permission: str = COMMAND
    timeout: float = 120.0


def parse_servers(raw: list[dict[str, Any]] | None) -> list[McpServerSpec]:
    out: list[McpServerSpec] = []
    seen: set[str] = set()
    for i, item in enumerate(raw or []):
        where = f"mcp_servers[{i}]"
        if not isinstance(item, dict):
            raise McpConfigError(f"{where}: expected a mapping")
        name = str(item.get("name", ""))
        if not _NAME.match(name):
            raise McpConfigError(f"{where}: name must be lowercase letters, digits and underscores, starting with a letter")
        if name in seen:
            raise McpConfigError(f"{where}: duplicate server name {name!r}")
        seen.add(name)
        cmd = item.get("command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
            raise McpConfigError(f"{where}: command must be a non-empty list of strings (an argument list, not a shell line)")
        env = item.get("env") or {}
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, (str, int, float)) for k, v in env.items()):
            raise McpConfigError(f"{where}: env must map names to values")
        perm = str(item.get("permission", "command"))
        if perm not in PERMISSIONS:
            raise McpConfigError(f"{where}: permission must be one of {', '.join(PERMISSIONS)}")
        try:
            timeout = float(item.get("timeout", 120))
        except (TypeError, ValueError) as exc:
            raise McpConfigError(f"{where}: timeout must be a number of seconds") from exc
        if not 0 < timeout <= 3600:
            raise McpConfigError(f"{where}: timeout must be between 0 and 3600 seconds")
        out.append(McpServerSpec(name, tuple(cmd), {k: str(v) for k, v in env.items()}, PERMISSIONS[perm], timeout))
    return out


def tool_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{re.sub(r'[^A-Za-z0-9_-]', '_', tool)}"


def describe_params(schema: dict[str, Any] | None) -> dict[str, str]:
    """A JSON-schema object's properties as the `name -> "type, meaning"` map the prompt uses."""
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    out: dict[str, str] = {}
    for key, p in props.items():
        typ = p.get("type", "any")
        typ = "/".join(typ) if isinstance(typ, list) else str(typ)
        bits = [typ]
        if p.get("description"):
            bits.append(str(p["description"]))
        if "default" in p:
            bits.append(f"default {p['default']!r}")
        bits.append("required" if key in required else "optional")
        out[key] = ", ".join(bits)
    return out


def result_text(result: Any) -> tuple[str, bool]:
    """(text, is_error) from a CallToolResult: text blocks joined, other block kinds named rather than dumped."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            parts.append(getattr(block, "text", ""))
        elif kind == "resource":
            parts.append(f"[resource {getattr(getattr(block, 'resource', None), 'uri', '')}]")
        else:
            parts.append(f"[{kind or 'unknown'} content omitted]")
    text = "\n".join(p for p in parts if p).strip()
    if not text and getattr(result, "structured_content", None) is not None:
        import json

        text = json.dumps(result.structured_content, ensure_ascii=False)
    return text, bool(getattr(result, "is_error", False))


class McpBridge:
    """Runs the servers on a private event loop in a background thread; calls from the agent are synchronous."""

    def __init__(self, specs: list[McpServerSpec], root: Path, client_factory: Callable[[McpServerSpec], Any] | None = None,
                 start_timeout: float = 60.0) -> None:
        self.specs, self.root = specs, Path(root)
        self._factory = client_factory or self._stdio_client
        self.start_timeout = start_timeout
        self.status: dict[str, str] = {}
        self._tools: list[ToolSpec] = []
        self._clients: dict[str, Any] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop: asyncio.Event | None = None
        self._tasks: list[Future] = []

    # ------------------------------------------------------------------------------------------------ start

    def _stdio_client(self, spec: McpServerSpec):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        return Client(StdioServerParameters(command=spec.argv[0], args=list(spec.argv[1:]), env=spec.env or None,
                                            cwd=str(self.root)))

    def start(self) -> dict[str, str]:
        """Connect to every server. Returns a status line per server: `ok: N tool(s)` or `error: reason`."""
        if not self.specs:
            return {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-bridge", daemon=True)
        self._thread.start()
        started = asyncio.run_coroutine_threadsafe(self._make_stop(), self._loop)
        self._stop = started.result(10)
        waits: list[tuple[McpServerSpec, Future]] = []
        for spec in self.specs:
            ready: Future = Future()
            self._tasks.append(asyncio.run_coroutine_threadsafe(self._serve(spec, ready), self._loop))
            waits.append((spec, ready))
        for spec, ready in waits:
            try:
                tools = ready.result(self.start_timeout)
                self._tools += tools
                self.status[spec.name] = f"ok: {len(tools)} tool(s)"
            except Exception as exc:  # noqa: BLE001 - one bad server must not stop the others or the session
                self.status[spec.name] = f"error: {type(exc).__name__}: {str(exc)[:200] or 'could not start'}"
        return dict(self.status)

    async def _make_stop(self) -> asyncio.Event:
        return asyncio.Event()

    async def _serve(self, spec: McpServerSpec, ready: Future) -> None:
        """One task per server, so the transport's cancel scopes are entered and left in the same task."""
        try:
            async with self._factory(spec) as client:
                listed = await asyncio.wait_for(client.list_tools(), self.start_timeout)
                tools = [self._wrap(spec, t) for t in listed.tools[:MAX_TOOLS_PER_SERVER]]
                self._clients[spec.name] = client
                ready.set_result(tools)
                assert self._stop is not None
                await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - report start-up failures to start(); later ones just end the task
            if not ready.done():
                ready.set_exception(exc if isinstance(exc, Exception) else RuntimeError(type(exc).__name__))
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            self._clients.pop(spec.name, None)

    def _wrap(self, spec: McpServerSpec, tool: Any) -> ToolSpec:
        desc = (getattr(tool, "description", None) or tool.name).strip().splitlines()[0][:200]
        return ToolSpec(tool_name(spec.name, tool.name), spec.permission, f"[MCP server {spec.name}] {desc}",
                        describe_params(getattr(tool, "input_schema", None)),
                        lambda _s=spec, _n=tool.name, **kw: self.call(_s, _n, kw))

    def tool_specs(self) -> list[ToolSpec]:
        return list(self._tools)

    # -------------------------------------------------------------------------------------------------- call

    def call(self, spec: McpServerSpec, tool: str, args: dict[str, Any]) -> str:
        client = self._clients.get(spec.name)
        if client is None or self._loop is None:
            raise ToolError(f"MCP server {spec.name} is not running")
        fut = asyncio.run_coroutine_threadsafe(asyncio.wait_for(client.call_tool(tool, args), spec.timeout), self._loop)
        try:
            result = fut.result(spec.timeout + 5)
        except TimeoutError as exc:
            fut.cancel()
            raise ToolError(f"MCP tool {tool} on {spec.name} timed out after {spec.timeout:g}s") from exc
        except Exception as exc:  # noqa: BLE001 - protocol and server errors go back to the model as a tool error
            raise ToolError(f"MCP tool {tool} on {spec.name} failed: {type(exc).__name__}: {str(exc)[:300]}") from exc
        text, is_error = result_text(result)
        if is_error:
            raise ToolError(text or f"MCP tool {tool} on {spec.name} reported an error")
        return text

    # ------------------------------------------------------------------------------------------------ close

    def close(self) -> None:
        if self._loop is None:
            return
        loop, self._loop = self._loop, None
        if self._stop is not None:
            loop.call_soon_threadsafe(self._stop.set)
        for t in self._tasks:
            try:
                t.result(10)
            except Exception:  # noqa: BLE001, S110 - shutting down; a task that failed already reported itself in start()
                pass
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(5)
        self._tasks.clear()
