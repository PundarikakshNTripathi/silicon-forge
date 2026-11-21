"""MCP server: tool registration and offline tool behavior (real-tool paths are covered by real_eda tests)."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")

from services.orchestrator import mcp_server  # noqa: E402
from tests.conftest import load_example  # noqa: E402


def test_tools_are_registered():
    tools = asyncio.run(mcp_server.server.list_tools())
    names = {t.name for t in tools}
    assert {"lint_rtl", "verify_rtl", "check_equivalence", "retrieve_skill", "list_examples", "run_example"} <= names


def test_offline_tools_work():
    assert "axi_lite_alu" in mcp_server.list_examples()
    assert mcp_server.retrieve_skill("sky130 clock budget")
    _, _, good, _ = load_example("uart_tx")
    assert mcp_server.lint_rtl(good)["passed"] is True
    bad = mcp_server.lint_rtl("module m(input logic clk); initial begin #5 end endmodule")
    assert bad["passed"] is False and bad["findings"]


def test_run_example_reports_provenance_and_rejects_unknown():
    r = mcp_server.run_example("uart_tx", backend="simulated", verify_only=True)
    assert r["provenance"] == "simulated" and r["lec_passed"] is True and r["error"] is None
    with pytest.raises(ValueError):
        mcp_server.run_example("nope")
