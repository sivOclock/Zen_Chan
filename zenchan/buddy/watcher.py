"""The watcher: a small loop that keeps up with your browsing and speaks up."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .. import ingest
from ..analysis import engine
from ..analysis.categorize import categorize_pending
from . import notify, nudges
from .events import BUS

log = logging.getLogger(__name__)


class Watcher:
    def __init__(self, zen, interval: float | None = None, analysis_every: float = 30 * 60,
                 publish: Callable[[dict], None] | None = None, notify_desktop: bool = True):
        self.zen = zen
        self.interval = interval or zen.settings.get("watch_interval", 60)
        self.analysis_every = analysis_every
        self.publish = publish or BUS.publish
        self.notify_desktop = notify_desktop
        self.stop_event = threading.Event()
        self.last_analysis = (zen.db.get_meta("last_analysis") or {}).get("ts", 0.0)
        self.thread: threading.Thread | None = None
        self.last_status: dict = {}

    def tick(self) -> dict:
        zen = self.zen
        report = ingest.sync(zen)
        if report["new_visits"]:
            categorize_pending(zen, None)          # fast rule-based pass; full ML runs periodically
        if time.time() - self.last_analysis >= self.analysis_every:
            self.publish({"type": "analysis", "phase": "start"})
            engine.analyze(zen)
            self.last_analysis = time.time()
            self.publish({"type": "analysis", "phase": "done", "ts": self.last_analysis})
        status = nudges.now_status(zen)
        created = nudges.evaluate(zen, status)
        for nudge in created:
            if self.notify_desktop:
                notify.deliver(zen, nudge)
            self.publish({"type": "nudge", **{k: v for k, v in nudge.items() if k != "dedup"}})
        self.last_status = nudges.public(status)
        self.publish({"type": "status", **self.last_status})
        return {"new_visits": report["new_visits"], "nudges": created, "status": self.last_status}

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:  # keep watching even if one source misbehaves
                log.exception("watcher tick failed")
            self.stop_event.wait(self.interval)

    def start(self) -> "Watcher":
        self.thread = threading.Thread(target=self.run_forever, name="zenchan-watcher", daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        self.stop_event.set()
