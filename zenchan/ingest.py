"""Pull history from every source into one normalized timeline."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from typing import Callable

from . import knowledge as K
from .analysis.text import clean_title, domain_of, is_mobile_url, search_query
from .browsers import LOCAL_DEVICE, READERS, SourceUnavailable, discover
from .core import Zen
from .timeutil import local

IDLE_GAP = 15 * 60        # longer silence than this = you walked away
VIDEO_IDLE_GAP = 3 * 3600 # ...unless you were watching something
VIDEO_TAIL_CAP = 45 * 60
TAIL_DEFAULT = 60         # assumed dwell on the last page before a break
TAIL_CAP = 20 * 60
MEASURED_CAP = 4 * 3600

INSERT = """INSERT OR IGNORE INTO visits
    (source_id, source_visit_id, ts, day, hour, weekday, url, domain, title, transition, device,
     raw_duration, duration, measured, search_query, scroll, mobile_url, feed, title_key)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

Progress = Callable[[str], None]


def fingerprint(path) -> str:
    """Cheap change detector: size + mtime of the DB and its WAL/journal."""
    parts = []
    for suffix in ("", "-wal", "-journal"):
        try:
            st = path.with_name(path.name + suffix).stat()
            parts.append(f"{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


def title_key(title: str) -> str | None:
    t = (title or "").strip().lower()
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16] if t else None


def is_feed(url: str, domain: str) -> bool:
    if K.NOT_FEED_URL.search(url):
        return False
    return domain in K.FEED_DOMAINS or bool(K.FEED_URL.search(url))


def visit_row(source_id: str, v, tz) -> tuple:
    dt = local(v.ts, tz)
    domain = domain_of(v.url)
    title = clean_title(v.title, domain)
    query = v.search_query or search_query(v.url, domain)
    return (source_id, v.source_visit_id, v.ts, dt.strftime("%Y-%m-%d"), dt.hour, dt.weekday(),
            v.url[:2000], domain, title[:500], v.transition, v.device,
            v.raw_duration or 0.0, (min(v.raw_duration, MEASURED_CAP) if v.measured else None),
            1 if v.measured else 0, query, v.scroll, 1 if is_mobile_url(v.url) else 0,
            1 if is_feed(v.url, domain) else 0, title_key(title))


def sync(zen: Zen, progress: Progress | None = None, full: bool = False, prune: bool = False) -> dict:
    emit = progress or (lambda _msg: None)
    db, settings, tz = zen.db, zen.settings, zen.tz
    profiles = discover(settings, imports_dir=zen.imports_dir)
    disabled = set(settings.get("disabled_sources", []))
    now = time.time()

    db.x("UPDATE sources SET seen=0")
    for prof in profiles:
        db.x("""INSERT INTO sources(id, browser, engine, profile, name, path, origin, seen)
                VALUES (?,?,?,?,?,?,?,1)
                ON CONFLICT(id) DO UPDATE SET browser=excluded.browser, name=excluded.name,
                path=excluded.path, origin=excluded.origin, seen=1""",
             (prof.source_id, prof.browser, prof.engine, prof.profile_id, prof.name,
              str(prof.history_path), prof.origin))
    emit(f"> found {len(profiles)} browser profile(s)")

    report = {"profiles": len(profiles), "new_visits": 0, "removed": 0, "sources": []}
    earliest_new = None
    for prof in profiles:
        sid = prof.source_id
        label = f"{prof.browser} / {prof.name}" + (f" ({prof.origin})" if prof.origin else "")
        if sid in disabled:
            db.x("UPDATE sources SET status='disabled' WHERE id=?", (sid,))
            continue
        reader = READERS[prof.engine]
        fp = fingerprint(prof.history_path)
        prev = db.one("SELECT last_ts, fingerprint, status FROM sources WHERE id=?", (sid,))
        if not full and not prune and prev and prev["fingerprint"] == fp and prev["status"] == "ok":
            continue                                   # nothing changed since the last read
        last_ts = 0.0 if full else (prev["last_ts"] if prev else 0.0) or 0.0
        emit(f"> reading {label}")
        status, detail, added, newest = "ok", "", 0, last_ts
        try:
            batch: list[tuple] = []
            conn = db.connect()
            before = conn.total_changes
            for visit in reader.read(prof, max(0.0, last_ts - 1)):
                batch.append(visit_row(sid, visit, tz))
                newest = max(newest, visit.ts)
                if earliest_new is None or visit.ts < earliest_new:
                    earliest_new = visit.ts
                if len(batch) >= 5000:
                    db.many(INSERT, batch)
                    batch.clear()
            if batch:
                db.many(INSERT, batch)
            added = conn.total_changes - before
            if prune and settings.get("mirror_deletions", True) and prof.engine != "import":
                removed = _prune(zen, prof, reader)
                report["removed"] += removed
        except SourceUnavailable as exc:
            status, detail = "blocked", str(exc)
            if prof.engine == "safari":
                detail += " — " + prof.extra.get("hint", "")
        except (sqlite3.DatabaseError, OSError, ValueError) as exc:
            status, detail = "error", f"{type(exc).__name__}: {exc}"
        count = db.scalar("SELECT COUNT(*) FROM visits WHERE source_id=?", (sid,), 0)
        db.x("UPDATE sources SET last_ts=?, last_sync=?, status=?, detail=?, visit_count=?, fingerprint=? WHERE id=?",
             (newest, now, status, detail, count, fp if status == "ok" else None, sid))
        report["new_visits"] += added
        report["sources"].append({"id": sid, "label": label, "status": status, "added": added, "detail": detail})
        if added:
            emit(f">   +{added} visits")
        elif status != "ok":
            emit(f">   {status}: {detail[:120]}")

    merged = merge_activity(zen)
    if merged:
        emit(f"> merged {merged} live activity span(s) from the extension")
    since = earliest_new if earliest_new is not None else (now - 6 * 3600 if merged else None)
    if full or report["removed"]:
        since = 0.0
    if since is not None:
        recompute_durations(zen, since)
    return report


def _prune(zen: Zen, prof, reader) -> int:
    """Forget visits the user deleted in the browser (keeps ones the browser merely expired)."""
    ids, oldest = reader.list_ids(prof)
    rows = zen.db.q("SELECT id, source_visit_id FROM visits WHERE source_id=? AND ts >= ?",
                    (prof.source_id, oldest))
    gone = [(r["id"],) for r in rows if r["source_visit_id"] not in ids]
    if gone:
        zen.db.many("DELETE FROM visits WHERE id=?", gone)
    return len(gone)


def merge_activity(zen: Zen) -> int:
    """Fold foreground-time spans reported by the browser extension into visits."""
    db, tz = zen.db, zen.tz
    spans = db.q("SELECT * FROM activity WHERE merged=0 AND end IS NOT NULL ORDER BY start")
    for span in spans:
        length = max(0.0, span["end"] - span["start"])
        match = db.one("""SELECT id FROM visits WHERE url=? AND ts BETWEEN ? AND ? AND measured=0
                          ORDER BY ABS(ts - ?) LIMIT 1""",
                       (span["url"], span["start"] - 60, span["start"] + 60, span["start"]))
        if match:
            db.x("UPDATE visits SET raw_duration=?, duration=?, measured=1 WHERE id=?",
                 (length, min(length, MEASURED_CAP), match["id"]))
        else:
            from .browsers.base import RawVisit
            visit = RawVisit(f"a{span['id']}", span["url"], span["title"] or "", span["start"],
                             raw_duration=length, measured=True, transition="live",
                             device=span["device"] or LOCAL_DEVICE)
            db.many(INSERT, [visit_row(f"live:{(span['browser'] or 'browser').lower()}", visit, tz)])
        db.x("UPDATE activity SET merged=1 WHERE id=?", (span["id"],))
    return len(spans)


def recompute_durations(zen: Zen, since_ts: float) -> int:
    """Attention-based dwell time.

    Browsers record how long a *tab* existed, not how long you looked at it, so
    summing their numbers wildly over-counts (ten background tabs = ten hours).
    Instead, per device we treat the browsing stream as one attention timeline:
    time on a page lasts until the next page you open anywhere (bounded by how
    long the tab actually lived, and by an idle gap that is longer for video).
    Real foreground measurements (Firefox engagement data, the extension)
    always win.
    """
    db = zen.db
    rows = db.q("""SELECT id, ts, device, raw_duration, measured, url FROM visits
                   WHERE ts >= ? ORDER BY device, ts""", (since_ts - VIDEO_IDLE_GAP,))
    updates: list[tuple] = []
    for i, row in enumerate(rows):
        if row["measured"]:
            updates.append((min(row["raw_duration"] or 0.0, MEASURED_CAP), row["id"]))
            continue
        nxt = rows[i + 1] if i + 1 < len(rows) and rows[i + 1]["device"] == row["device"] else None
        gap = (nxt["ts"] - row["ts"]) if nxt else None
        raw = row["raw_duration"] or 0.0
        video = bool(K.LONG_DWELL_URL.search(row["url"] or ""))
        idle = VIDEO_IDLE_GAP if video else IDLE_GAP
        if gap is not None and gap <= idle:
            # in front until the next page opened anywhere, but never longer than the tab lived
            duration = min(gap, raw) if raw > 0 else gap
        elif gap is None and time.time() - row["ts"] <= idle:
            duration = time.time() - row["ts"]          # still on this page right now
        elif video:
            duration = min(raw, VIDEO_TAIL_CAP) if raw > 0 else 10 * 60
        else:
            duration = min(raw, TAIL_CAP) if raw > 0 else TAIL_DEFAULT
        updates.append((max(0.0, duration), row["id"]))
    for start in range(0, len(updates), 5000):
        db.many("UPDATE visits SET duration=? WHERE id=?", updates[start:start + 5000])
    return len(updates)
