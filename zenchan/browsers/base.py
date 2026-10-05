"""Shared types for browser history readers."""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

LOCAL_DEVICE = "this-device"


@dataclass
class Profile:
    """One browser profile (or one imported file) that has history."""

    browser: str                 # "Chrome", "Firefox", "Safari", "Google Takeout", ...
    engine: str                  # chromium | firefox | safari | import
    profile_id: str              # directory name, e.g. "Default", "Profile 3", "abcd.default-release"
    name: str                    # human name shown in the UI
    history_path: Path
    profile_dir: Optional[Path] = None
    avatar_path: Optional[Path] = None
    origin: str = ""             # e.g. "windows (WSL)" when read across an OS boundary
    extra: dict = field(default_factory=dict)

    @property
    def source_id(self) -> str:
        raw = f"{self.engine}:{self.browser}:{self.origin}:{self.profile_id}"
        return re.sub(r"[^A-Za-z0-9:._-]+", "-", raw).strip("-").lower()

    def to_dict(self) -> dict:
        return {
            "id": self.source_id,
            "browser": self.browser,
            "engine": self.engine,
            "profile": self.profile_id,
            "name": self.name,
            "path": str(self.history_path),
            "origin": self.origin,
            "has_avatar": bool(self.avatar_path and self.avatar_path.exists()),
        }


@dataclass
class RawVisit:
    source_visit_id: str
    url: str
    title: str
    ts: float                         # unix seconds, UTC
    raw_duration: float = 0.0         # what the browser recorded (seconds), 0 if unknown
    measured: bool = False            # True if raw_duration is real foreground time
    transition: str = ""
    device: str = LOCAL_DEVICE
    search_query: Optional[str] = None
    scroll: Optional[float] = None    # Firefox engagement metadata, when available


class SourceUnavailable(Exception):
    """Raised when history exists but cannot be read (permissions, corruption)."""


@contextmanager
def open_snapshot(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a consistent read-only copy of a live browser database.

    Browsers keep their history DB open (and on Windows sometimes locked), so we
    copy it plus its WAL/rollback journal into a temp dir and read the copy. If
    copying is refused because of a lock we fall back to an immutable read-only
    open of the original file.
    """
    db_path = Path(db_path)
    tmpdir = Path(tempfile.mkdtemp(prefix="zenchan-"))
    conn = None
    try:
        try:
            dst = tmpdir / db_path.name
            shutil.copy2(db_path, dst)
            for suffix in ("-wal", "-journal"):
                side = db_path.with_name(db_path.name + suffix)
                if side.exists():
                    try:
                        shutil.copy2(side, tmpdir / side.name)
                    except OSError:
                        pass
            conn = sqlite3.connect(str(dst))
        except PermissionError as exc:
            raise SourceUnavailable(f"permission denied reading {db_path}") from exc
        except OSError:
            uri = db_path.resolve().as_uri() + "?mode=ro&immutable=1"
            conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        except sqlite3.DatabaseError as exc:
            raise SourceUnavailable(f"{db_path.name} is not a readable database ({exc})") from exc
        yield conn
    finally:
        if conn is not None:
            conn.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def expand(template: str, values: dict[str, str]) -> list[Path]:
    """Fill ``{PLACEHOLDER}``s and expand ``*`` globs; returns existing paths only."""
    try:
        filled = template.format(**values)
    except KeyError:
        return []
    if "*" not in filled:
        p = Path(filled)
        return [p] if p.exists() else []
    path = Path(filled)
    anchor = Path(path.anchor) if path.anchor else Path(".")
    rel = str(path.relative_to(anchor)) if path.anchor else filled
    try:
        return sorted(p for p in anchor.glob(rel) if p.exists())
    except (OSError, ValueError):
        return []
