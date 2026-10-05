"""Operating-system detection and per-OS directory conventions."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

WINDOWS, MACOS, LINUX, ANDROID = "windows", "macos", "linux", "android"


def os_name() -> str:
    if sys.platform.startswith("win"):
        return WINDOWS
    if sys.platform == "darwin":
        return MACOS
    if "TERMUX_VERSION" in os.environ or "ANDROID_ROOT" in os.environ or hasattr(sys, "getandroidapilevel"):
        return ANDROID
    return LINUX


def is_wsl() -> bool:
    if os_name() != LINUX:
        return False
    try:
        return "microsoft" in platform.release().lower()
    except Exception:
        return False


def user_home() -> Path:
    override = os.environ.get("ZENCHAN_USER_HOME")
    return Path(override) if override else Path.home()


def data_dir() -> Path:
    """Where Zen-chan keeps its own database and settings."""
    override = os.environ.get("ZENCHAN_HOME")
    if override:
        path = Path(override)
    else:
        osn, home = os_name(), Path.home()
        if osn == WINDOWS:
            base = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
            path = Path(base) / "ZenChan"
        elif osn == MACOS:
            path = home / "Library" / "Application Support" / "ZenChan"
        else:
            base = os.environ.get("XDG_DATA_HOME") or str(home / ".local" / "share")
            path = Path(base) / "zenchan"
    path.mkdir(parents=True, exist_ok=True)
    return path


def placeholders(osn: str, home: Path, env: dict | None = None) -> dict[str, str]:
    """Values substituted into browser path templates such as ``{LOCALAPPDATA}``."""
    env = os.environ if env is None else env
    h = str(home)
    if osn == WINDOWS:
        return {
            "HOME": h,
            "LOCALAPPDATA": env.get("LOCALAPPDATA") or str(home / "AppData" / "Local"),
            "APPDATA": env.get("APPDATA") or str(home / "AppData" / "Roaming"),
        }
    if osn == MACOS:
        return {"HOME": h, "APPSUPPORT": str(home / "Library" / "Application Support")}
    if osn == ANDROID:
        return {"HOME": h, "ANDROID_DATA": env.get("ZENCHAN_ANDROID_DATA", "/data/data")}
    return {
        "HOME": h,
        "CONFIG": env.get("XDG_CONFIG_HOME") or str(home / ".config"),
        "SNAP": str(home / "snap"),
        "FLATPAK": str(home / ".var" / "app"),
    }


def wsl_windows_homes() -> list[Path]:
    """Windows user profiles visible from inside WSL (``/mnt/c/Users/*``)."""
    root = Path("/mnt/c/Users")
    if not is_wsl() or not root.is_dir():
        return []
    skip = {"public", "default", "default user", "all users", "wdagutilityaccount"}
    try:
        return [p for p in root.iterdir() if p.is_dir() and p.name.lower() not in skip]
    except OSError:
        return []


def describe() -> dict:
    return {
        "os": os_name(),
        "wsl": is_wsl(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "node": platform.node(),
    }
