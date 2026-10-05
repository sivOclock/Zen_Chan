"""Firefox, LibreWolf, Waterfox, Zen, Floorp, Mullvad & other Gecko browsers."""

from __future__ import annotations

import bisect
import configparser
from pathlib import Path
from typing import Iterator

from .base import LOCAL_DEVICE, Profile, RawVisit, expand, open_snapshot, table_columns

# browser name -> {os: [directories that contain profiles.ini]}
BROWSERS: dict[str, dict[str, list[str]]] = {
    "Firefox": {
        "windows": ["{APPDATA}/Mozilla/Firefox"],
        "macos": ["{APPSUPPORT}/Firefox"],
        "linux": ["{HOME}/.mozilla/firefox", "{CONFIG}/mozilla/firefox",
                  "{SNAP}/firefox/common/.mozilla/firefox",
                  "{FLATPAK}/org.mozilla.firefox/.mozilla/firefox",
                  "{FLATPAK}/org.mozilla.firefox/config/mozilla/firefox"],
        "android": ["{ANDROID_DATA}/org.mozilla.firefox/files/mozilla"],
    },
    "Firefox Nightly": {"android": ["{ANDROID_DATA}/org.mozilla.fenix/files/mozilla"]},
    "LibreWolf": {
        "windows": ["{APPDATA}/librewolf"],
        "macos": ["{APPSUPPORT}/librewolf"],
        "linux": ["{HOME}/.librewolf", "{FLATPAK}/io.gitlab.librewolf-community/.librewolf"],
    },
    "Waterfox": {
        "windows": ["{APPDATA}/Waterfox"],
        "macos": ["{APPSUPPORT}/Waterfox"],
        "linux": ["{HOME}/.waterfox"],
    },
    "Zen": {
        "windows": ["{APPDATA}/zen"],
        "macos": ["{APPSUPPORT}/zen"],
        "linux": ["{HOME}/.zen", "{FLATPAK}/app.zen_browser.zen/.zen"],
    },
    "Floorp": {
        "windows": ["{APPDATA}/Floorp"],
        "macos": ["{APPSUPPORT}/Floorp"],
        "linux": ["{HOME}/.floorp", "{FLATPAK}/one.ablaze.floorp/.floorp"],
    },
    "Mullvad Browser": {
        "windows": ["{APPDATA}/Mullvad/MullvadBrowser"],
        "macos": ["{APPSUPPORT}/MullvadBrowser"],
        "linux": ["{HOME}/.mullvad-browser"],
    },
}

# moz_historyvisits.visit_type
VISIT_TYPES = {1: "link", 2: "typed", 3: "bookmark", 4: "embed", 5: "redirect", 6: "redirect",
               7: "download", 8: "subframe", 9: "reload"}
SKIP_TYPES = {4, 7, 8}
SOURCE_SYNCED = 4  # moz_historyvisits.source


def profiles_in(base: Path, browser: str, origin: str = "") -> list[Profile]:
    found: dict[Path, Profile] = {}
    ini = base / "profiles.ini"
    if ini.is_file():
        parser = configparser.RawConfigParser()
        try:
            parser.read(ini, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError):
            parser = configparser.RawConfigParser()
        for section in parser.sections():
            if not section.lower().startswith("profile"):
                continue
            rel = parser.get(section, "Path", fallback="")
            if not rel:
                continue
            is_rel = parser.get(section, "IsRelative", fallback="1") == "1"
            pdir = (base / rel) if is_rel else Path(rel)
            places = pdir / "places.sqlite"
            if places.is_file():
                name = parser.get(section, "Name", fallback=pdir.name)
                found[pdir.resolve()] = Profile(browser, "firefox", pdir.name, name, places,
                                                profile_dir=pdir, origin=origin)
    # Fallback for missing/odd profiles.ini (also covers Android's flat layout).
    for pattern in ("*/places.sqlite", "Profiles/*/places.sqlite"):
        try:
            for places in base.glob(pattern):
                pdir = places.parent
                if pdir.resolve() not in found:
                    found[pdir.resolve()] = Profile(browser, "firefox", pdir.name, pdir.name.split(".")[-1],
                                                    places, profile_dir=pdir, origin=origin)
        except OSError:
            continue
    return sorted(found.values(), key=lambda p: p.profile_id)


def _engagement(conn, since_ms: int) -> dict[int, list[tuple]]:
    """Firefox's moz_places_metadata records real foreground time & scrolling."""
    cols = table_columns(conn, "moz_places_metadata")
    if not {"place_id", "created_at", "total_view_time"} <= cols:
        return {}
    scroll = "scrolling_distance" if "scrolling_distance" in cols else "0"
    out: dict[int, list[tuple]] = {}
    for row in conn.execute(
        f"SELECT place_id, created_at, total_view_time, {scroll} FROM moz_places_metadata "
        "WHERE created_at > ? ORDER BY created_at", (since_ms - 600_000,)
    ):
        out.setdefault(row[0], []).append((row[1], row[2] or 0, row[3] or 0))
    return out


def read(profile: Profile, since_ts: float = 0) -> Iterator[RawVisit]:
    with open_snapshot(profile.history_path) as conn:
        vcols = table_columns(conn, "moz_historyvisits")
        source_col = "v.source" if "source" in vcols else "0"
        engagement = _engagement(conn, int(since_ts * 1000))
        used: set[tuple[int, int]] = set()
        query = f"""
            SELECT v.id AS vid, v.place_id, p.url, p.title, v.visit_date, v.visit_type,
                   {source_col} AS src
            FROM moz_historyvisits v JOIN moz_places p ON p.id = v.place_id
            WHERE v.visit_date > ?
            ORDER BY v.visit_date
        """
        for row in conn.execute(query, (int(since_ts * 1_000_000),)):
            vtype = row["visit_type"] or 1
            url = row["url"] or ""
            if vtype in SKIP_TYPES or not url.startswith(("http://", "https://")):
                continue
            ts = row["visit_date"] / 1_000_000
            duration, scroll, measured = 0.0, None, False
            meta = engagement.get(row["place_id"])
            if meta:
                visit_ms = int(ts * 1000)
                keys = [m[0] for m in meta]
                i = bisect.bisect_left(keys, visit_ms - 2_000)
                while i < len(meta) and meta[i][0] <= visit_ms + 120_000:
                    if (row["place_id"], i) not in used:
                        used.add((row["place_id"], i))
                        duration, scroll, measured = meta[i][1] / 1000, float(meta[i][2]), True
                        break
                    i += 1
            yield RawVisit(
                source_visit_id=str(row["vid"]),
                url=url,
                title=row["title"] or "",
                ts=ts,
                raw_duration=duration,
                measured=measured and duration > 0,
                transition=VISIT_TYPES.get(vtype, "other"),
                device="firefox-sync" if row["src"] == SOURCE_SYNCED else LOCAL_DEVICE,
                scroll=scroll,
            )


def list_ids(profile: Profile) -> tuple[set[str], float]:
    with open_snapshot(profile.history_path) as conn:
        rows = conn.execute("SELECT id, visit_date FROM moz_historyvisits").fetchall()
    if not rows:
        return set(), 0.0
    return {str(r[0]) for r in rows}, min(r[1] for r in rows) / 1_000_000
