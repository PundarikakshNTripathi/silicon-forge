# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite (WAL) store for runs, their event streams and app settings."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL, status TEXT NOT NULL,
    stage TEXT, spec_text TEXT, profile TEXT, backend TEXT, module TEXT, signoff INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0, tokens INTEGER DEFAULT 0, duration_s REAL, error TEXT, request_json TEXT,
    state_json TEXT, cost_json TEXT
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL, ts REAL, stage TEXT, status TEXT, detail TEXT, payload TEXT,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created DESC);
"""

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    # --- runs -----------------------------------------------------------------------------------------

    def create_run(self, run_id: str, spec_text: str, profile: str, backend: str, request: dict[str, Any]) -> None:
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO runs (id, created, updated, status, stage, spec_text, profile, backend, request_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, now, now, "queued", "INIT", spec_text, profile, backend, json.dumps(request)),
            )
            self._db.commit()

    def set_status(self, run_id: str, status: str, stage: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE runs SET status=?, stage=COALESCE(?, stage), updated=? WHERE id=?",
                (status, stage, time.time(), run_id),
            )
            self._db.commit()

    def finish_run(self, run_id: str, status: str, state: dict[str, Any] | None, cost: dict[str, Any] | None,
                   duration_s: float, error: str | None) -> None:
        contract = (state or {}).get("contract") or {}
        with self._lock:
            self._db.execute(
                "UPDATE runs SET status=?, stage=?, module=?, signoff=?, cost_usd=?, tokens=?, duration_s=?, error=?, "
                "state_json=?, cost_json=?, updated=? WHERE id=?",
                (
                    status, (state or {}).get("stage"), contract.get("module_name"),
                    1 if (state or {}).get("signoff_passed") else 0,
                    (cost or {}).get("total_cost_usd", 0.0), (cost or {}).get("total_tokens", 0),
                    round(duration_s, 2), error, json.dumps(state, default=str) if state else None,
                    json.dumps(cost, default=str) if cost else None, time.time(), run_id,
                ),
            )
            self._db.commit()

    def update_progress(self, run_id: str, stage: str, module: str | None = None) -> None:
        with self._lock:
            self._db.execute("UPDATE runs SET stage=?, module=COALESCE(?, module), updated=? WHERE id=?",
                             (stage, module, time.time(), run_id))
            self._db.commit()

    def list_runs(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, created, updated, status, stage, profile, backend, module, signoff, cost_usd, tokens, "
                "duration_s, error FROM runs ORDER BY created DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        for key in ("request_json", "state_json", "cost_json"):
            d[key.removesuffix("_json")] = json.loads(d.pop(key)) if d.get(key) else None
        return d

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM events WHERE run_id=?", (run_id,))
            self._db.execute("DELETE FROM runs WHERE id=?", (run_id,))
            self._db.commit()

    def mark_interrupted(self) -> int:
        """Runs left in flight by a previous process can never finish: mark them interrupted."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE runs SET status='interrupted', error='Backend restarted while the run was in progress.' "
                "WHERE status IN ('queued','running')")
            self._db.commit()
            return cur.rowcount

    # --- events ---------------------------------------------------------------------------------------

    def add_event(self, run_id: str, ev: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO events (run_id, seq, ts, stage, status, detail, payload) VALUES (?,?,?,?,?,?,?)",
                (run_id, ev["seq"], ev.get("ts"), ev["stage"], ev["status"], ev["detail"],
                 json.dumps(ev.get("payload") or {}, default=str)),
            )
            self._db.commit()

    def events_after(self, run_id: str, after: int = -1, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, ts, stage, status, detail, payload FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                (run_id, after, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    # --- settings -------------------------------------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def put_settings(self, values: dict[str, Any]) -> None:
        with self._lock:
            for k, v in values.items():
                self._db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, json.dumps(v)))
            self._db.commit()

    # --- aggregate ------------------------------------------------------------------------------------

    def cost_rollup(self) -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute(
                "SELECT profile, COUNT(*) n, COALESCE(SUM(cost_usd),0) usd, COALESCE(SUM(tokens),0) tok, "
                "SUM(signoff) ok FROM runs GROUP BY profile").fetchall()
            total = self._db.execute("SELECT COALESCE(SUM(cost_usd),0), COALESCE(SUM(tokens),0), COUNT(*) FROM runs").fetchone()
        return {
            "total_usd": round(total[0], 6), "total_tokens": total[1], "runs": total[2],
            "by_profile": [{"profile": r["profile"], "runs": r["n"], "usd": round(r["usd"], 6), "tokens": r["tok"],
                            "signoff_passed": r["ok"]} for r in rows],
        }
