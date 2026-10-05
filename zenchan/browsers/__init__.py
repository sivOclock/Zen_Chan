"""Find every browser profile on this machine, on any OS."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .. import platforms
from . import chromium, firefox, imports, safari
from .base import LOCAL_DEVICE, Profile, RawVisit, SourceUnavailable

READERS = {"chromium": chromium, "firefox": firefox, "safari": safari, "import": imports}

__all__ = ["discover", "READERS", "Profile", "RawVisit", "SourceUnavailable", "LOCAL_DEVICE"]


def scan_os(osn: str, home: Path, env: dict | None = None, origin: str = "") -> list[Profile]:
    """Probe every known browser location for one operating system."""
    values = platforms.placeholders(osn, home, env)
    found: list[Profile] = []
    for browser, table in chromium.BROWSERS.items():
        for template in table.get(osn, []):
            for user_data in _expand(template, values):
                found += chromium.profiles_in(user_data, browser, origin)
    for browser, table in firefox.BROWSERS.items():
        for template in table.get(osn, []):
            for base in _expand(template, values):
                found += firefox.profiles_in(base, browser, origin)
    if osn == platforms.MACOS:
        for browser, template in safari.LOCATIONS:
            for directory in _expand(template, values):
                found += safari.profiles_in(directory, browser, origin)
    return found


def _expand(template: str, values: dict) -> list[Path]:
    from .base import expand
    try:
        return expand(template, values)
    except OSError:
        return []


def discover(settings=None, osn: str | None = None, home: Path | None = None,
             env: dict | None = None, imports_dir: Path | None = None) -> list[Profile]:
    osn = osn or platforms.os_name()
    home = home or platforms.user_home()
    found: list[Profile] = []
    discover_system = True if settings is None else settings.get("discover_system", True)

    if discover_system:
        found += scan_os(osn, home, env)
        if osn == platforms.LINUX:
            for win_home in platforms.wsl_windows_homes():
                found += scan_os(platforms.WINDOWS, win_home, env={}, origin="windows")

    for extra in (settings.get("extra_sources", []) if settings else []):
        found += _from_extra(extra)

    if imports_dir and imports_dir.is_dir():
        for path in sorted(imports_dir.iterdir()):
            if path.suffix.lower() in imports.SUFFIXES:
                found.append(imports.describe(path))

    unique: dict[str, Profile] = {}
    for prof in found:
        try:
            key = str(prof.history_path.resolve())
        except OSError:
            key = str(prof.history_path)
        unique.setdefault(key, prof)
    return list(unique.values())


def _from_extra(extra: dict) -> Iterable[Profile]:
    engine = extra.get("engine", "chromium")
    path = Path(extra.get("path", "")).expanduser()
    browser = extra.get("browser") or engine.title()
    origin = extra.get("origin", "")
    if not path.exists():
        return []
    if engine == "chromium":
        return chromium.profiles_in(path, browser, origin)
    if engine == "firefox":
        return firefox.profiles_in(path, browser, origin)
    if engine == "safari":
        return safari.profiles_in(path, browser, origin)
    if engine == "import" and path.is_file():
        return [imports.describe(path)]
    return []
