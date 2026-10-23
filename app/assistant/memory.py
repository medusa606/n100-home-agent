"""SQLite-backed conversation history, pending confirmations, traces and a small key/value store."""
import json
import sqlite3
import time
from pathlib import Path

PENDING_TTL = 600  # seconds a confirmation stays valid

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, chat_id TEXT, role TEXT, content TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS pending (chat_id TEXT PRIMARY KEY, tool TEXT, args TEXT, prompt TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS traces (id INTEGER PRIMARY KEY, run_id TEXT, chat_id TEXT, ts REAL,
                                   step INTEGER, kind TEXT, name TEXT, detail TEXT, ms INTEGER);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


class Memory:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)

    # --- conversation ---
    def add_turn(self, chat_id: str, role: str, content: str) -> None:
        self.db.execute("INSERT INTO turns (chat_id, role, content, ts) VALUES (?,?,?,?)",
                        (chat_id, role, content, time.time()))
        self.db.commit()

    def history(self, chat_id: str, turns: int) -> list[dict]:
        rows = self.db.execute("SELECT role, content FROM turns WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                               (chat_id, turns * 2)).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    def reset(self, chat_id: str) -> None:
        self.db.execute("DELETE FROM turns WHERE chat_id=?", (chat_id,))
        self.db.execute("DELETE FROM pending WHERE chat_id=?", (chat_id,))
        self.db.commit()

    # --- human-in-the-loop confirmations ---
    def set_pending(self, chat_id: str, tool: str, args: dict, prompt: str) -> None:
        self.db.execute("REPLACE INTO pending VALUES (?,?,?,?,?)",
                        (chat_id, tool, json.dumps(args), prompt, time.time()))
        self.db.commit()

    def pop_pending(self, chat_id: str) -> tuple[str, dict] | None:
        row = self.db.execute("SELECT tool, args, ts FROM pending WHERE chat_id=?", (chat_id,)).fetchone()
        self.db.execute("DELETE FROM pending WHERE chat_id=?", (chat_id,))
        self.db.commit()
        if not row or time.time() - row[2] > PENDING_TTL:
            return None
        return row[0], json.loads(row[1])

    # --- observability ---
    def trace(self, run_id: str, chat_id: str, step: int, kind: str, name: str, detail: str, ms: int) -> None:
        self.db.execute("INSERT INTO traces (run_id, chat_id, ts, step, kind, name, detail, ms) VALUES (?,?,?,?,?,?,?,?)",
                        (run_id, chat_id, time.time(), step, kind, name, detail[:500], ms))
        self.db.commit()

    def last_trace(self, chat_id: str) -> list[tuple]:
        row = self.db.execute("SELECT run_id FROM traces WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
        if not row:
            return []
        return self.db.execute("SELECT step, kind, name, detail, ms FROM traces WHERE run_id=? ORDER BY id",
                               (row[0],)).fetchall()

    # --- key/value (LinkedIn token etc.) ---
    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        self.db.execute("REPLACE INTO kv VALUES (?,?)", (key, value))
        self.db.commit()
