"""Persistent SQLite response cache (exact-match on model + params + prompt + schema).

This is an exact-match cache, not a semantic one: it only helps when an identical request repeats (re-runs of the same
spec, resumed runs, retried stages). It is only consulted for low-temperature calls, where a replayed response is as
valid as a fresh sample. Provider/server prompt-prefix caching is a separate, complementary layer.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

from services.orchestrator.inference.providers.base import LLMResponse


class ResponseCache:
    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._mem: dict[str, LLMResponse] = {}
        self._db: sqlite3.Connection | None = None
        if path is not None:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(p, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, body TEXT NOT NULL, "
                "created REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0)"
            )
            self._db.commit()

    @staticmethod
    def key(provider: str, model: str, system_prompt: str, user_prompt: str, temperature: float,
            json_schema: dict | None) -> str:
        raw = json.dumps(
            [provider, model, round(temperature, 3), system_prompt, user_prompt, json_schema], sort_keys=True
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> LLMResponse | None:
        with self._lock:
            if key in self._mem:
                return self._mem[key]
            if self._db is None:
                return None
            row = self._db.execute("SELECT body FROM responses WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE responses SET hits = hits + 1 WHERE key=?", (key,))
            self._db.commit()
            resp = LLMResponse(**json.loads(row[0]))
            self._mem[key] = resp
            return resp

    def put(self, key: str, resp: LLMResponse) -> None:
        with self._lock:
            self._mem[key] = resp
            if self._db is not None:
                body = json.dumps(resp.__dict__, default=str)
                self._db.execute(
                    "INSERT OR REPLACE INTO responses (key, body, created, hits) VALUES (?, ?, ?, 0)",
                    (key, body, time.time()),
                )
                self._db.commit()

    def stats(self) -> dict:
        with self._lock:
            if self._db is None:
                return {"entries": len(self._mem), "persistent": False}
            n, hits = self._db.execute("SELECT COUNT(*), COALESCE(SUM(hits),0) FROM responses").fetchone()
            return {"entries": n, "hits": hits, "persistent": True}

    def clear(self) -> None:
        with self._lock:
            self._mem.clear()
            if self._db is not None:
                self._db.execute("DELETE FROM responses")
                self._db.commit()
