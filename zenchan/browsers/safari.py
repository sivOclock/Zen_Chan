"""Safari on macOS — including iPhone/iPad history that arrives via iCloud."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .base import LOCAL_DEVICE, Profile, RawVisit, open_snapshot, table_columns

MAC_EPOCH_OFFSET = 978_307_200  # seconds between 1970-01-01 and 2001-01-01

LOCATIONS = [
    ("Safari", "{HOME}/Library/Safari"),
    ("Safari Technology Preview", "{HOME}/Library/SafariTechnologyPreview"),
    ("Safari", "{HOME}/Library/Safari/Profiles/*"),
    ("Safari", "{HOME}/Library/Containers/com.apple.Safari/Data/Library/Safari/Profiles/*"),
]

FULL_DISK_ACCESS_HINT = (
    "macOS protects Safari history. Open System Settings > Privacy & Security > "
    "Full Disk Access and enable it for your terminal (or Python), then re-sync."
)


def profiles_in(directory: Path, browser: str, origin: str = "") -> list[Profile]:
    history = directory / "History.db"
    exists = False
    try:
        exists = history.is_file()
    except PermissionError:
        exists = True  # we can see the folder but not into it: report it as needing access
    if not exists:
        return []
    in_profiles = directory.parent.name == "Profiles"
    pid = directory.name[:8] if in_profiles else "Default"
    name = f"Profile {pid}" if in_profiles else "Default"
    return [Profile(browser, "safari", pid, name, history, profile_dir=directory, origin=origin,
                    extra={"hint": FULL_DISK_ACCESS_HINT})]


def read(profile: Profile, since_ts: float = 0) -> Iterator[RawVisit]:
    with open_snapshot(profile.history_path) as conn:
        vcols = table_columns(conn, "history_visits")
        origin_col = "v.origin" if "origin" in vcols else "0"
        redirect_col = "v.redirect_destination" if "redirect_destination" in vcols else "NULL"
        ok_col = "v.load_successful" if "load_successful" in vcols else "1"
        query = f"""
            SELECT v.id AS vid, i.url, v.title, v.visit_time, {origin_col} AS origin,
                   {redirect_col} AS redirect_to, {ok_col} AS ok
            FROM history_visits v JOIN history_items i ON i.id = v.history_item
            WHERE v.visit_time > ?
            ORDER BY v.visit_time
        """
        for row in conn.execute(query, (since_ts - MAC_EPOCH_OFFSET,)):
            url = row["url"] or ""
            if row["redirect_to"] is not None or row["ok"] == 0:
                continue
            if not url.startswith(("http://", "https://")):
                continue
            yield RawVisit(
                source_visit_id=str(row["vid"]),
                url=url,
                title=row["title"] or "",
                ts=row["visit_time"] + MAC_EPOCH_OFFSET,
                transition="",
                device="icloud" if row["origin"] == 1 else LOCAL_DEVICE,
            )


def list_ids(profile: Profile) -> tuple[set[str], float]:
    with open_snapshot(profile.history_path) as conn:
        rows = conn.execute("SELECT id, visit_time FROM history_visits").fetchall()
    if not rows:
        return set(), 0.0
    return {str(r[0]) for r in rows}, min(r[1] for r in rows) + MAC_EPOCH_OFFSET
