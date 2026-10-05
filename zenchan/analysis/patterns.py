"""Behavioural patterns worth knowing about."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from ..timeutil import is_late, local
from .sessions import longest_chain, longest_feed_run
from .text import is_worry_search, registrable, tokens

CHECK_GAP = 10 * 60


def doomscroll_episodes(sessions_visits: list[list[dict]], threshold_min: float, tz=None, goals=None) -> list[dict]:
    goals = goals or {}
    out = []
    for vs in sessions_visits:
        minutes, i, j = longest_feed_run(vs)
        if minutes >= threshold_min and j >= i:
            run = vs[i:j + 1]
            sites = Counter(v["domain"] for v in run if v["feed"])
            start = local(run[0]["ts"], tz)
            out.append({
                "start": run[0]["ts"], "minutes": round(minutes, 1), "items": len(run),
                "sites": [d for d, _ in sites.most_common(3)], "device": run[0]["device"],
                "when": start.strftime("%a %d %b, %H:%M"),
                "late": is_late(start, goals.get("bedtime", "23:30"), goals.get("wake", "06:30")),
                "seconds_per_item": round(minutes * 60 / max(len(run), 1)),
            })
    return sorted(out, key=lambda e: -e["start"])


def rabbit_holes(sessions_visits: list[list[dict]], tz=None, min_len: int = 6) -> list[dict]:
    out = []
    for vs in sessions_visits:
        length, i, j = longest_chain(vs)
        if length >= min_len:
            chain = vs[i:j + 1]
            out.append({
                "start": chain[0]["ts"], "hops": length, "site": registrable(chain[0]["domain"]),
                "from": chain[0]["title"], "to": chain[-1]["title"],
                "path": [v["title"] for v in chain][:20],
                "minutes": round(sum(v["duration"] or 0 for v in chain) / 60, 1),
                "when": local(chain[0]["ts"], tz).strftime("%a %d %b, %H:%M"),
            })
    return sorted(out, key=lambda e: -e["start"])


def compulsive_checks(visits: list[dict], days: int, min_per_day: float = 6) -> list[dict]:
    """Domains you keep re-opening in short bursts ("just checking")."""
    last_seen: dict[tuple, float] = {}
    checks: Counter = Counter()
    burst_secs: defaultdict = defaultdict(float)
    for v in sorted(visits, key=lambda v: v["ts"]):
        site = registrable(v["domain"])
        key = (v["device"], site)
        prev = last_seen.get(key)
        if prev is None or v["ts"] - prev > CHECK_GAP:
            checks[site] += 1
        burst_secs[site] += v["duration"] or 0
        last_seen[key] = v["ts"] + (v["duration"] or 0)
    out = []
    for site, n in checks.items():
        per_day = n / max(days, 1)
        avg_min = burst_secs[site] / n / 60
        if per_day >= min_per_day and avg_min <= 6:
            out.append({"site": site, "checks": n, "per_day": round(per_day, 1), "avg_minutes": round(avg_min, 1)})
    return sorted(out, key=lambda e: -e["per_day"])[:10]


def search_spirals(sessions_visits: list[list[dict]], min_size: int = 4, tz=None) -> list[dict]:
    """Many near-identical searches in one session: stuck, anxious, or both."""
    out = []
    for vs in sessions_visits:
        queries = [(v["ts"], v["search_query"]) for v in vs if v["search_query"]]
        if len(queries) < min_size:
            continue
        groups: list[dict] = []
        for ts, q in queries:
            toks = set(tokens(q))
            for g in groups:
                inter = len(toks & g["tokens"])
                if toks and inter / len(toks | g["tokens"]) >= 0.34:
                    g["queries"].append(q)
                    g["tokens"] |= toks
                    break
            else:
                groups.append({"tokens": set(toks), "queries": [q], "start": ts})
        for g in groups:
            if len(g["queries"]) >= min_size:
                out.append({"start": g["start"], "count": len(g["queries"]),
                            "queries": list(dict.fromkeys(g["queries"]))[:6],
                            "worry": any(is_worry_search(q) for q in g["queries"]),
                            "when": local(g["start"], tz).strftime("%a %d %b, %H:%M")})
    return sorted(out, key=lambda e: -e["start"])


def switching(visits: list[dict]) -> dict:
    """Context switches per active hour."""
    by_device: defaultdict = defaultdict(list)
    for v in visits:
        by_device[v["device"]].append(v)
    switches, active = 0, 0.0
    for vs in by_device.values():
        vs.sort(key=lambda v: v["ts"])
        switches += sum(1 for a, b in zip(vs, vs[1:]) if a["domain"] != b["domain"] and b["ts"] - a["ts"] < 600)
        active += sum(v["duration"] or 0 for v in vs)
    return {"switches": switches, "per_hour": round(switches / max(active / 3600, 0.25), 1)}


def late_nights(visits: list[dict], goals: dict, tz=None) -> dict:
    bedtime, wake = goals.get("bedtime", "23:30"), goals.get("wake", "06:30")
    minutes, nights = 0.0, set()
    latest_by_night: dict[str, str] = {}
    for v in visits:
        dt = local(v["ts"], tz)
        if is_late(dt, bedtime, wake):
            minutes += (v["duration"] or 0) / 60
            night = (dt if dt.hour >= 12 else dt - timedelta(days=1)).strftime("%Y-%m-%d")  # 1am belongs to last night
            nights.add(night)
            stamp = dt.strftime("%H:%M")
            if dt.hour < 12:
                latest_by_night[night] = max(latest_by_night.get(night, "00:00"), stamp)
    latest = max(latest_by_night.values()) if latest_by_night else None
    return {"minutes": round(minutes), "nights": len(nights), "latest": latest}
