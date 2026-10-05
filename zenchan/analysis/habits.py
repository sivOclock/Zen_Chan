"""Habit model: when you browse, what leads where, and what usually comes next."""

from __future__ import annotations

from collections import Counter, defaultdict

from .. import knowledge as K
from ..timeutil import local
from .text import registrable


def heatmap(visits: list[dict]) -> list[list[float]]:
    """Minutes by [weekday][hour]."""
    grid = [[0.0] * 24 for _ in range(7)]
    for v in visits:
        grid[v["weekday"]][v["hour"]] += (v["duration"] or 0) / 60
    return [[round(x, 1) for x in row] for row in grid]


def transitions(sessions_visits: list[list[dict]], min_count: int = 3) -> dict:
    """Markov chain over categories within sessions."""
    counts: Counter = Counter()
    for vs in sessions_visits:
        cats = [v["category"] or "Other" for v in vs if v["category"] not in (None, "Search")]
        collapsed = [c for i, c in enumerate(cats) if i == 0 or c != cats[i - 1]]
        for a, b in zip(collapsed, collapsed[1:]):
            counts[(a, b)] += 1
    out_totals: Counter = Counter()
    for (a, _b), n in counts.items():
        out_totals[a] += n
    edges = [{"from": a, "to": b, "n": n, "p": round(n / out_totals[a], 3)}
             for (a, b), n in counts.items() if n >= min_count]
    edges.sort(key=lambda e: -e["n"])
    # gateways: what most often leads *into* leisure categories
    into: defaultdict = defaultdict(Counter)
    for (a, b), n in counts.items():
        if b in K.LEISURE and a not in K.LEISURE:
            into[b][a] += n
    gateways = []
    for target, sources in into.items():
        total = sum(sources.values())
        src, n = sources.most_common(1)[0]
        if total >= 4:
            gateways.append({"to": target, "from": src, "share": round(n / total, 2), "n": total})
    gateways.sort(key=lambda g: -g["n"])
    return {"edges": edges[:40], "gateways": gateways[:6]}


def rituals(visits: list[dict], days: int, tz=None) -> list[dict]:
    """Sites you open within the first 20 minutes of most days (a day starts at 04:00)."""
    first_ts: dict[str, float] = {}
    for v in visits:
        if v["hour"] >= 4:
            first_ts[v["day"]] = min(first_ts.get(v["day"], v["ts"]), v["ts"])
    hits: defaultdict = defaultdict(set)
    hours: defaultdict = defaultdict(list)
    for v in visits:
        start = first_ts.get(v["day"])
        if start is None or v["ts"] < start:
            continue
        if v["ts"] - start <= 20 * 60:
            site = registrable(v["domain"])
            hits[site].add(v["day"])
            hours[site].append(local(v["ts"], tz).hour + local(v["ts"], tz).minute / 60)
    n_days = max(len(first_ts), 1)
    out = []
    for site, ds in hits.items():
        if len(ds) / n_days >= 0.5 and len(ds) >= 3:
            hs = sorted(hours[site])
            median = hs[len(hs) // 2]
            out.append({"site": site, "days": len(ds), "share": round(len(ds) / n_days, 2),
                        "typical": f"{int(median):02d}:{int((median % 1) * 60):02d}"})
    return sorted(out, key=lambda r: -r["share"])[:6]


def forecast(visits: list[dict], weekday: int, hour: int) -> list[dict]:
    """What you usually do at this hour on this kind of day (weekday vs weekend)."""
    weekend = weekday >= 5
    cat: Counter = Counter()
    days = set()
    for v in visits:
        if (v["weekday"] >= 5) == weekend and abs(v["hour"] - hour) <= 0:
            cat[v["category"] or "Other"] += v["duration"] or 0
            days.add(v["day"])
    total = sum(cat.values())
    if total < 600 or len(days) < 2:
        return []
    return [{"category": c, "share": round(s / total, 2), "minutes_typical": round(s / 60 / len(days))}
            for c, s in cat.most_common(3)]


def forecast_table(visits: list[dict], min_days: int = 2) -> dict[str, list[dict]]:
    """For each (weekday|weekend, hour): your usual category mix. Used by the live watcher."""
    minutes: defaultdict = defaultdict(Counter)
    days: defaultdict = defaultdict(set)
    for v in visits:
        key = f"{'weekend' if v['weekday'] >= 5 else 'weekday'}:{v['hour']}"
        minutes[key][v["category"] or "Other"] += (v["duration"] or 0) / 60
        days[key].add(v["day"])
    table = {}
    for key, cats in minutes.items():
        total = sum(cats.values())
        if total >= 10 and len(days[key]) >= min_days:
            table[key] = [{"category": c, "share": round(m / total, 2)} for c, m in cats.most_common(3)]
    return table
