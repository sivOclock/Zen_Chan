"""User settings, persisted as JSON next to the database."""

from __future__ import annotations

import copy
import json
import secrets
import threading
from pathlib import Path

from .platforms import data_dir

DEFAULTS: dict = {
    # Discovery
    "discover_system": True,          # scan the real browser locations on this machine
    "extra_sources": [],              # [{"engine": "chromium"|"firefox"|"safari", "path": "...", "browser": "..."}]
    "disabled_sources": [],           # source ids the user switched off
    "mirror_deletions": True,         # if you delete history in the browser, forget it here too
    "device_aliases": {},             # {"chrome-sync:1a2b3c4d": "Pixel 8"}
    "timezone": "",                   # IANA name; empty = system local time

    # What "intentional" means for this person
    "goals": {
        "productive": ["Programming & Dev", "Work & Productivity", "Learning & Education",
                       "Research & Reference", "Science", "Jobs & Career"],
        "limits": {"Social Media": 60, "Video & Streaming": 120},   # minutes per day
        "bedtime": "23:30",
        "wake": "06:30",
    },

    # The watcher
    "nudges": {
        "enabled": True,
        "doomscroll_minutes": 20,
        "binge_minutes": 90,
        "switches_per_30min": 45,
        "compulsive_checks": 10,
        "search_spiral": 4,
        "focus_praise_minutes": 50,
        "regret_threshold": 0.75,
        "forecast": True,
        "cooldown_minutes": 25,
    },
    "notify": {"desktop": True, "ntfy_url": ""},
    "watch_interval": 60,

    # The narrator ("buddy voice")
    "narrator": {
        "backend": "local",          # local | ollama | anthropic
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "llama3.2",
        "anthropic_model": "claude-opus-5-5",
        "share_titles": False,       # LLM backends only see aggregates unless this is on
    },

    # ML
    "ml": {"embedder": "auto", "map_points": 4000},

    # Server
    "server": {"port": 7777, "token": ""},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key not in ("device_aliases", "limits"):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Settings:
    """Thread-safe JSON-backed settings."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else data_dir() / "settings.json"
        self._lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> dict:
        raw = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        data = _merge(DEFAULTS, raw)
        if not data["server"].get("token"):
            data["server"]["token"] = secrets.token_urlsafe(18)
            self.data = data
            self.save()
        return data

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def update(self, patch: dict) -> dict:
        self.data = _merge(self.data, patch)
        self.save()
        return self.data

    def public(self) -> dict:
        """Settings safe to send to the dashboard (no secrets)."""
        out = copy.deepcopy(self.data)
        out["server"] = {"port": out["server"].get("port")}
        return out

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def token(self) -> str:
        return self.data["server"]["token"]
