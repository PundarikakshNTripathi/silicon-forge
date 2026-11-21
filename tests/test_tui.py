# SPDX-License-Identifier: AGPL-3.0-or-later
"""Headless test of the terminal UI: start a replay run and watch it complete."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.widgets import DataTable, Static  # noqa: E402

from services.tui.app import SiliconForgeTUI, stage_states  # noqa: E402


def test_stage_states_from_events():
    ev = [
        {"stage": "ARCHITECT", "status": "PASS"}, {"stage": "RTL", "status": "VERSION"},
        {"stage": "GUARDRAIL_AUDIT", "status": "PASS"}, {"stage": "FORMAL_VERIFY", "status": "RETRY"},
    ]
    st = stage_states(ev)
    assert st["ARCHITECT"] == "done" and st["RTL"] == "done" and st["FORMAL_VERIFY"] == "active"
    assert st["SIGNOFF"] == "pending"
    assert stage_states(ev + [{"stage": "FAILED", "status": "FAIL"}])["FORMAL_VERIFY"] == "fail"


def test_tui_runs_a_replay_design_end_to_end(tmp_path):
    async def scenario() -> None:
        app = SiliconForgeTUI(db_path=tmp_path / "t.db", runs_root=tmp_path / "runs")
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.click("#start")
            for _ in range(200):
                await pilot.pause(0.1)
                run = app.store.get_run(app.run_id) if app.run_id else None
                if run and run["status"] in ("succeeded", "failed"):
                    break
            await pilot.pause(0.6)
            assert run and run["status"] == "succeeded", run
            assert app.query_one("#log", DataTable).row_count >= 8
            banner = str(app.query_one("#banner", Static).render())
            assert "SIMULATED" in banner
            stats = str(app.query_one("#stats", Static).render())
            assert "passed" in stats and "signoff closed" in stats

    asyncio.run(scenario())
