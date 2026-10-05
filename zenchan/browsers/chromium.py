"""Chrome, Edge, Brave, Opera, Vivaldi, Arc, Chromium & friends.

All Chromium-family browsers share the same ``History`` SQLite schema and the
same ``Local State`` JSON for profile names, so one reader covers them all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .base import LOCAL_DEVICE, Profile, RawVisit, expand, open_snapshot, table_columns

WEBKIT_EPOCH_OFFSET = 11_644_473_600  # seconds between 1601-01-01 and 1970-01-01

# browser name -> {os: [user-data-dir templates]}
BROWSERS: dict[str, dict[str, list[str]]] = {
    "Chrome": {
        "windows": ["{LOCALAPPDATA}/Google/Chrome/User Data"],
        "macos": ["{APPSUPPORT}/Google/Chrome"],
        "linux": ["{CONFIG}/google-chrome", "{FLATPAK}/com.google.Chrome/config/google-chrome"],
        "android": ["{ANDROID_DATA}/com.android.chrome/app_chrome"],
    },
    "Chrome Beta": {
        "windows": ["{LOCALAPPDATA}/Google/Chrome Beta/User Data"],
        "macos": ["{APPSUPPORT}/Google/Chrome Beta"],
        "linux": ["{CONFIG}/google-chrome-beta"],
        "android": ["{ANDROID_DATA}/com.chrome.beta/app_chrome"],
    },
    "Chrome Dev": {
        "windows": ["{LOCALAPPDATA}/Google/Chrome Dev/User Data"],
        "macos": ["{APPSUPPORT}/Google/Chrome Dev"],
        "linux": ["{CONFIG}/google-chrome-unstable"],
    },
    "Chrome Canary": {
        "windows": ["{LOCALAPPDATA}/Google/Chrome SxS/User Data"],
        "macos": ["{APPSUPPORT}/Google/Chrome Canary"],
    },
    "Chromium": {
        "windows": ["{LOCALAPPDATA}/Chromium/User Data"],
        "macos": ["{APPSUPPORT}/Chromium"],
        "linux": ["{CONFIG}/chromium", "{SNAP}/chromium/common/chromium",
                  "{FLATPAK}/org.chromium.Chromium/config/chromium",
                  "{FLATPAK}/io.github.ungoogled_software.ungoogled_chromium/config/chromium"],
    },
    "Edge": {
        "windows": ["{LOCALAPPDATA}/Microsoft/Edge/User Data"],
        "macos": ["{APPSUPPORT}/Microsoft Edge"],
        "linux": ["{CONFIG}/microsoft-edge", "{FLATPAK}/com.microsoft.Edge/config/microsoft-edge"],
        "android": ["{ANDROID_DATA}/com.microsoft.emmx/app_chrome"],
    },
    "Edge Beta": {
        "windows": ["{LOCALAPPDATA}/Microsoft/Edge Beta/User Data"],
        "macos": ["{APPSUPPORT}/Microsoft Edge Beta"],
        "linux": ["{CONFIG}/microsoft-edge-beta"],
    },
    "Edge Dev": {
        "windows": ["{LOCALAPPDATA}/Microsoft/Edge Dev/User Data"],
        "macos": ["{APPSUPPORT}/Microsoft Edge Dev"],
        "linux": ["{CONFIG}/microsoft-edge-dev"],
    },
    "Brave": {
        "windows": ["{LOCALAPPDATA}/BraveSoftware/Brave-Browser/User Data"],
        "macos": ["{APPSUPPORT}/BraveSoftware/Brave-Browser"],
        "linux": ["{CONFIG}/BraveSoftware/Brave-Browser",
                  "{FLATPAK}/com.brave.Browser/config/BraveSoftware/Brave-Browser",
                  "{SNAP}/brave/current/.config/BraveSoftware/Brave-Browser"],
        "android": ["{ANDROID_DATA}/com.brave.browser/app_chrome"],
    },
    "Vivaldi": {
        "windows": ["{LOCALAPPDATA}/Vivaldi/User Data"],
        "macos": ["{APPSUPPORT}/Vivaldi"],
        "linux": ["{CONFIG}/vivaldi", "{FLATPAK}/com.vivaldi.Vivaldi/config/vivaldi"],
        "android": ["{ANDROID_DATA}/com.vivaldi.browser/app_chrome"],
    },
    "Opera": {
        "windows": ["{APPDATA}/Opera Software/Opera Stable"],
        "macos": ["{APPSUPPORT}/com.operasoftware.Opera"],
        "linux": ["{CONFIG}/opera", "{SNAP}/opera/current/.config/opera"],
    },
    "Opera GX": {
        "windows": ["{APPDATA}/Opera Software/Opera GX Stable"],
        "macos": ["{APPSUPPORT}/com.operasoftware.OperaGX"],
    },
    "Arc": {
        "windows": ["{LOCALAPPDATA}/Packages/TheBrowserCompany.Arc*/LocalCache/Local/Arc/User Data"],
        "macos": ["{APPSUPPORT}/Arc/User Data"],
    },
    "Dia": {"macos": ["{APPSUPPORT}/Dia/User Data"]},
    "Comet": {
        "windows": ["{LOCALAPPDATA}/Perplexity/Comet/User Data"],
        "macos": ["{APPSUPPORT}/Comet"],
    },
    "Yandex": {
        "windows": ["{LOCALAPPDATA}/Yandex/YandexBrowser/User Data"],
        "macos": ["{APPSUPPORT}/Yandex/YandexBrowser"],
        "linux": ["{CONFIG}/yandex-browser"],
    },
    "Whale": {
        "windows": ["{LOCALAPPDATA}/Naver/Naver Whale/User Data"],
        "macos": ["{APPSUPPORT}/Naver/Whale"],
        "linux": ["{CONFIG}/naver-whale"],
    },
    "Thorium": {
        "windows": ["{LOCALAPPDATA}/Thorium/User Data"],
        "macos": ["{APPSUPPORT}/Thorium"],
        "linux": ["{CONFIG}/thorium"],
    },
    "Samsung Internet": {"android": ["{ANDROID_DATA}/com.sec.android.app.sbrowser/app_sbrowser"]},
    "Kiwi": {"android": ["{ANDROID_DATA}/com.kiwibrowser.browser/app_chrome"]},
}

# Core page-transition types (transition & 0xFF)
TRANSITIONS = {
    0: "link", 1: "typed", 2: "bookmark", 3: "subframe", 4: "subframe", 5: "generated",
    6: "toplevel", 7: "form", 8: "reload", 9: "keyword", 10: "keyword",
}
CHAIN_START, CHAIN_END = 0x10000000, 0x20000000
CLIENT_REDIRECT, SERVER_REDIRECT = 0x40000000, 0x80000000


def webkit_to_unix(us: int | float) -> float:
    return float(us) / 1_000_000 - WEBKIT_EPOCH_OFFSET


def unix_to_webkit(ts: float) -> int:
    return int((ts + WEBKIT_EPOCH_OFFSET) * 1_000_000)


def is_user_visible(transition: int) -> bool:
    """Drop iframe loads and the intermediate hops of redirect chains."""
    t = int(transition) & 0xFFFFFFFF
    if (t & 0xFF) in (3, 4):
        return False
    if not (t & CHAIN_END) and (t & (CHAIN_START | CLIENT_REDIRECT | SERVER_REDIRECT)):
        return False
    return True


def _profile_names(user_data: Path) -> dict[str, dict]:
    try:
        state = json.loads((user_data / "Local State").read_text(encoding="utf-8"))
        return state.get("profile", {}).get("info_cache", {}) or {}
    except (OSError, ValueError):
        return {}


def _avatar(profile_dir: Path) -> Path | None:
    pic = profile_dir / "Google Profile Picture.png"
    if pic.exists():
        return pic
    avatars = profile_dir / "Accounts" / "Avatar Images"
    if avatars.is_dir():
        files = [p for p in avatars.iterdir() if p.is_file()]
        if files:
            return max(files, key=lambda p: p.stat().st_mtime)
    return None


def profiles_in(user_data: Path, browser: str, origin: str = "") -> list[Profile]:
    """All profiles inside one Chromium "User Data" directory."""
    found: list[Profile] = []
    if (user_data / "History").is_file():
        # Opera-style: the user-data dir *is* the profile.
        found.append(Profile(browser, "chromium", "Default", browser, user_data / "History",
                             profile_dir=user_data, origin=origin))
    names = _profile_names(user_data)
    candidates = set(names)
    try:
        candidates |= {p.name for p in user_data.iterdir() if (p / "History").is_file()}
    except OSError:
        pass
    side = user_data / "_side_profiles"  # Opera side profiles
    if side.is_dir():
        candidates |= {f"_side_profiles/{p.name}" for p in side.iterdir() if (p / "History").is_file()}
    for pid in sorted(candidates):
        pdir = user_data / pid
        history = pdir / "History"
        if not history.is_file() or pid in ("System Profile", "Guest Profile"):
            continue
        info = names.get(pid, {})
        name = info.get("name") or info.get("gaia_given_name") or pid
        found.append(Profile(browser, "chromium", pid, name, history, profile_dir=pdir,
                             avatar_path=_avatar(pdir), origin=origin))
    return found


def read(profile: Profile, since_ts: float = 0) -> Iterator[RawVisit]:
    with open_snapshot(profile.history_path) as conn:
        vcols = table_columns(conn, "visits")
        guid_col = "v.originator_cache_guid" if "originator_cache_guid" in vcols else "NULL"
        terms: dict[int, str] = {}
        if table_columns(conn, "keyword_search_terms"):
            for row in conn.execute("SELECT url_id, term FROM keyword_search_terms"):
                if row["term"]:
                    terms[row["url_id"]] = row["term"]
        query = f"""
            SELECT v.id AS vid, u.id AS uid, u.url, u.title, v.visit_time, v.visit_duration,
                   v.transition, {guid_col} AS guid
            FROM visits v JOIN urls u ON u.id = v.url
            WHERE v.visit_time > ?
            ORDER BY v.visit_time
        """
        for row in conn.execute(query, (unix_to_webkit(since_ts),)):
            transition = row["transition"] or 0
            if not is_user_visible(transition):
                continue
            url = row["url"] or ""
            if not url.startswith(("http://", "https://")):
                continue
            guid = (row["guid"] or "").strip()
            yield RawVisit(
                source_visit_id=str(row["vid"]),
                url=url,
                title=row["title"] or "",
                ts=webkit_to_unix(row["visit_time"]),
                raw_duration=(row["visit_duration"] or 0) / 1_000_000,
                transition=TRANSITIONS.get(int(transition) & 0xFF, "other"),
                device=f"chrome-sync:{guid[:8]}" if guid else LOCAL_DEVICE,
                search_query=terms.get(row["uid"]),
            )


def list_ids(profile: Profile) -> tuple[set[str], float]:
    """All visit ids still present in the browser, and the oldest timestamp."""
    with open_snapshot(profile.history_path) as conn:
        rows = conn.execute("SELECT id, visit_time FROM visits").fetchall()
    if not rows:
        return set(), 0.0
    return {str(r[0]) for r in rows}, webkit_to_unix(min(r[1] for r in rows))
