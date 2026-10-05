"""Local-time helpers. Everything is stored as UTC epoch seconds; days and hours
are derived in the user's timezone (system local unless configured)."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None


def get_tz(name: str | None) -> tzinfo | None:
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            return None
    return None


def local(ts: float, tz: tzinfo | None = None) -> datetime:
    return datetime.fromtimestamp(ts, tz) if tz else datetime.fromtimestamp(ts).astimezone()


def now_local(tz: tzinfo | None = None) -> datetime:
    return datetime.now(tz) if tz else datetime.now().astimezone()


def day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def window_bounds(window: str, tz: tzinfo | None = None, now: datetime | None = None) -> tuple[float, float]:
    """'today' | '7d' | '30d' | '90d' | 'all' -> (start_ts, end_ts)."""
    now = now or now_local(tz)
    end = now.timestamp()
    if window == "today":
        return day_start(now).timestamp(), end
    if window == "yesterday":
        start = day_start(now) - timedelta(days=1)
        return start.timestamp(), day_start(now).timestamp()
    if window.endswith("d") and window[:-1].isdigit():
        start = day_start(now) - timedelta(days=int(window[:-1]) - 1)
        return start.timestamp(), end
    return 0.0, end


def hhmm_to_minutes(value: str, default: int) -> int:
    try:
        h, m = value.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return default


def is_late(dt: datetime, bedtime: str = "23:30", wake: str = "06:30") -> bool:
    """True when ``dt`` falls between bedtime and wake time."""
    minute = dt.hour * 60 + dt.minute
    bed, wk = hhmm_to_minutes(bedtime, 23 * 60 + 30), hhmm_to_minutes(wake, 6 * 60 + 30)
    if bed > wk:
        return minute >= bed or minute < wk
    return bed <= minute < wk


def fmt_minutes(minutes: float) -> str:
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if m else f"{h}h"
