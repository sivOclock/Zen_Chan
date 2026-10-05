"""SQLite storage for normalized visits, sessions, insights and nudges."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .platforms import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, browser TEXT, engine TEXT, profile TEXT, name TEXT, path TEXT,
    origin TEXT, last_ts REAL DEFAULT 0, last_sync REAL, status TEXT DEFAULT 'new',
    detail TEXT, visit_count INTEGER DEFAULT 0, seen INTEGER DEFAULT 1, fingerprint TEXT
);

CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL, source_visit_id TEXT NOT NULL,
    ts REAL NOT NULL, day TEXT, hour INTEGER, weekday INTEGER,
    url TEXT, domain TEXT, title TEXT, transition TEXT, device TEXT,
    raw_duration REAL DEFAULT 0, duration REAL, measured INTEGER DEFAULT 0,
    search_query TEXT, scroll REAL, mobile_url INTEGER DEFAULT 0,
    category TEXT, category_conf REAL, cat_method TEXT,
    emotion TEXT, valence REAL, feed INTEGER DEFAULT 0, title_key TEXT,
    session_id INTEGER,
    UNIQUE (source_id, source_visit_id)
);
CREATE INDEX IF NOT EXISTS ix_visits_ts ON visits(ts);
CREATE INDEX IF NOT EXISTS ix_visits_device_ts ON visits(device, ts);
CREATE INDEX IF NOT EXISTS ix_visits_domain ON visits(domain);
CREATE INDEX IF NOT EXISTS ix_visits_session ON visits(session_id);
CREATE INDEX IF NOT EXISTS ix_visits_uncat ON visits(category) WHERE category IS NULL;

CREATE TABLE IF NOT EXISTS titles (
    key TEXT PRIMARY KEY, title TEXT, domain TEXT, model TEXT, vec BLOB,
    topic INTEGER, x2 REAL, y2 REAL, x3 REAL, y3 REAL, z3 REAL
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY, label TEXT, keywords TEXT, size INTEGER, minutes REAL,
    category TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY, device TEXT, start REAL, end REAL, day TEXT,
    visits INTEGER, active_sec REAL, domains INTEGER, switches INTEGER,
    top_category TEXT, top_domain TEXT, state TEXT, scores TEXT, features TEXT,
    regret REAL, label TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_start ON sessions(start);

-- user feedback survives re-sessionization by being keyed on device + start time
CREATE TABLE IF NOT EXISTS session_labels (
    device TEXT, start REAL, label TEXT, features TEXT, created REAL,
    PRIMARY KEY (device, start)
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY, device TEXT, browser TEXT, url TEXT, title TEXT,
    start REAL, end REAL, merged INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_activity_start ON activity(start);

CREATE TABLE IF NOT EXISTS insights (key TEXT PRIMARY KEY, computed REAL, payload TEXT);

CREATE TABLE IF NOT EXISTS nudges (
    id INTEGER PRIMARY KEY, ts REAL, kind TEXT, severity TEXT, title TEXT, body TEXT,
    data TEXT, dedup TEXT UNIQUE, seen INTEGER DEFAULT 0, delivered INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_nudges_ts ON nudges(ts);
"""


class Database:
    """Thin wrapper: one connection per thread, WAL mode, dict rows."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else data_dir() / "zenchan.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.write_lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=OFF")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # helpers -----------------------------------------------------------------
    def q(self, sql: str, params=()) -> list[sqlite3.Row]:
        return self.connect().execute(sql, params).fetchall()

    def one(self, sql: str, params=()):
        return self.connect().execute(sql, params).fetchone()

    def scalar(self, sql: str, params=(), default=None):
        row = self.one(sql, params)
        return default if row is None or row[0] is None else row[0]

    def x(self, sql: str, params=()) -> sqlite3.Cursor:
        with self.write_lock:
            conn = self.connect()
            cur = conn.execute(sql, params)
            conn.commit()
            return cur

    def many(self, sql: str, rows) -> None:
        with self.write_lock:
            conn = self.connect()
            conn.executemany(sql, rows)
            conn.commit()

    def get_meta(self, key: str, default=None):
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value) -> None:
        self.x("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def put_insight(self, key: str, payload: dict) -> None:
        self.x("INSERT OR REPLACE INTO insights(key, computed, payload) VALUES (?, ?, ?)",
               (key, time.time(), json.dumps(payload, default=_json_default)))

    def get_insight(self, key: str):
        row = self.one("SELECT computed, payload FROM insights WHERE key=?", (key,))
        if not row:
            return None
        payload = json.loads(row["payload"])
        payload.setdefault("computed", row["computed"])
        return payload

    def wipe(self) -> None:
        with self.write_lock:
            conn = self.connect()
            for table in ("visits", "titles", "topics", "sessions", "session_labels", "activity",
                          "insights", "nudges", "sources", "meta"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
            conn.execute("VACUUM")


def _json_default(obj):
    try:
        import numpy as np
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    raise TypeError(f"not JSON serializable: {type(obj)}")
