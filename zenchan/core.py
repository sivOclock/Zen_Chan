"""The application context shared by the CLI, watcher and web server."""

from __future__ import annotations

from pathlib import Path

from .config import Settings
from .db import Database
from .platforms import data_dir
from .timeutil import get_tz


class Zen:
    def __init__(self, home: Path | str | None = None):
        self.home = Path(home) if home else data_dir()
        self.home.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(self.home / "settings.json")
        self.db = Database(self.home / "zenchan.db")
        self.imports_dir = self.home / "imports"
        self.imports_dir.mkdir(exist_ok=True)

    @property
    def tz(self):
        return get_tz(self.settings.get("timezone"))
