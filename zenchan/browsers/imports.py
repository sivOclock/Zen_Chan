"""Imported history files: Google Takeout, Safari export, generic JSON/CSV.

This is the bridge for phones and browsers whose databases we can't open
directly. Google Takeout's ``BrowserHistory.json`` includes Android Chrome
history (with a per-device ``client_id``); Safari 18's "Export Browsing Data"
produces ``History.json``; anything else can come in as CSV.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .base import LOCAL_DEVICE, Profile, RawVisit

SUFFIXES = {".json", ".csv", ".zip"}


def _to_unix(value) -> float | None:
    """Accept µs/ms/s epoch numbers or ISO-8601 strings."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().lstrip("-").replace(".", "", 1).isdigit()):
        num = float(value)
        if num > 1e17:      # nanoseconds
            return num / 1e9
        if num > 1e14:      # microseconds
            return num / 1e6
        if num > 1e11:      # milliseconds
            return num / 1e3
        return num
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.astimezone()  # naive -> local time
        return dt.astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _records_from_json(data) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("Browser History", "history", "History", "visits", "items"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
    return []


def _iter_records(path: Path) -> Iterator[dict]:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                low = name.lower()
                if low.endswith(("browserhistory.json", "history.json")):
                    yield from _records_from_json(json.loads(zf.read(name).decode("utf-8-sig")))
                elif low.endswith(".csv"):
                    yield from csv.DictReader(io.StringIO(zf.read(name).decode("utf-8-sig")))
        return
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if suffix == ".json":
        yield from _records_from_json(json.loads(text))
    else:
        yield from csv.DictReader(io.StringIO(text))


def _pick(record: dict, *keys):
    lowered = {str(k).lower(): v for k, v in record.items()}
    for key in keys:
        if key in lowered and lowered[key] not in (None, ""):
            return lowered[key]
    return None


def describe(path: Path) -> Profile:
    digest = hashlib.sha1(str(path.name).encode()).hexdigest()[:6]
    name = path.stem
    browser = "Google Takeout" if "browserhistory" in name.lower() else "Import"
    return Profile(browser, "import", f"{name}-{digest}", name, path)


def read(profile: Profile, since_ts: float = 0) -> Iterator[RawVisit]:
    for record in _iter_records(profile.history_path):
        url = _pick(record, "url", "uri", "link", "address")
        ts = _to_unix(_pick(record, "time_usec", "visittime", "visit_time", "lastvisittime",
                            "timestamp", "time", "date", "datetime", "visited"))
        if not url or ts is None or ts <= since_ts or not str(url).startswith(("http://", "https://")):
            continue
        client = _pick(record, "client_id", "device", "device_id")
        title = _pick(record, "title", "name", "page_title") or ""
        duration = _to_seconds(_pick(record, "duration", "duration_sec", "time_spent"))
        key = hashlib.sha1(f"{url}|{ts:.3f}".encode()).hexdigest()[:16]
        yield RawVisit(
            source_visit_id=key,
            url=str(url),
            title=str(title),
            ts=ts,
            raw_duration=duration,
            measured=duration > 0,
            transition=str(_pick(record, "page_transition", "transition") or "").lower(),
            device=f"takeout:{str(client)[:8]}" if client else LOCAL_DEVICE,
        )


def _to_seconds(value) -> float:
    try:
        return max(0.0, float(value)) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def list_ids(profile: Profile) -> tuple[set[str], float]:
    visits = list(read(profile))
    return {v.source_visit_id for v in visits}, min((v.ts for v in visits), default=0.0)
