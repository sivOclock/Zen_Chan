"""Unusual days, judged against *your own* recent baseline."""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from .. import knowledge as K
from ..timeutil import is_late, local
from .text import is_worry_search, registrable

# feature -> (human label, noise floor so tiny baselines don't explode)
FEATURES = {
    "minutes": ("browsing time", 20.0),
    "late_min": ("late-night browsing", 10.0),
    "feed_min": ("feed scrolling", 10.0),
    "leisure_share": ("share of leisure", 0.08),
    "switch_rate": ("tab/site switching", 8.0),
    "worry": ("worry searches", 1.0),
    "new_sites": ("never-seen-before sites", 3.0),
}


def daily_table(visits: list[dict], goals: dict, first_seen: dict[str, str], tz=None) -> dict[str, dict]:
    days: dict[str, dict] = defaultdict(lambda: {k: 0.0 for k in FEATURES} | {"_leisure": 0.0, "_switches": 0})
    bedtime, wake = goals.get("bedtime", "23:30"), goals.get("wake", "06:30")
    prev_by_device: dict[str, dict] = {}
    for v in sorted(visits, key=lambda v: v["ts"]):
        d = days[v["day"]]
        minutes = (v["duration"] or 0) / 60
        d["minutes"] += minutes
        if is_late(local(v["ts"], tz), bedtime, wake):
            d["late_min"] += minutes
        if v["feed"]:
            d["feed_min"] += minutes
        if v["category"] in K.LEISURE:
            d["_leisure"] += minutes
        if is_worry_search(v["search_query"]):
            d["worry"] += 1
        prev = prev_by_device.get(v["device"])
        if prev and prev["domain"] != v["domain"] and v["ts"] - prev["ts"] < 600:
            d["_switches"] += 1
        prev_by_device[v["device"]] = v
    for site, day in first_seen.items():
        if day in days:
            days[day]["new_sites"] += 1
    for d in days.values():
        d["leisure_share"] = d["_leisure"] / d["minutes"] if d["minutes"] else 0.0
        d["switch_rate"] = d["_switches"] / max(d["minutes"] / 60, 0.25)
    return {day: {k: round(v, 3) for k, v in vals.items() if not k.startswith("_")} for day, vals in days.items()}


def detect(daily: dict[str, dict], check_days: list[str], min_baseline: int = 5,
           today: str | None = None) -> list[dict]:
    order = sorted(daily)
    iso = _isolation_scores(daily)
    out = []
    for day in check_days:
        if day not in daily:
            continue
        idx = order.index(day)
        prior = [daily[d] for d in order[max(0, idx - 28):idx]]
        if len(prior) < min_baseline:
            continue
        reasons, zmax = [], 0.0
        for feat, (label, floor) in FEATURES.items():
            series = np.array([p[feat] for p in prior], dtype=float)
            med = float(np.median(series))
            mad = float(np.median(np.abs(series - med)))
            x = daily[day][feat]
            z = (x - med) / (1.4826 * mad + floor)
            zmax = max(zmax, abs(z))
            if z >= 2.0 and x - med >= floor:
                ratio = x / max(med, floor)
                if feat == "leisure_share":
                    reasons.append(f"{x:.0%} leisure vs your usual {med:.0%}")
                elif feat in ("worry", "new_sites"):
                    reasons.append(f"{int(x)} {label} (usually ~{med:.0f})")
                else:
                    reasons.append(f"{ratio:.1f}x your usual {label}")
            elif feat == "minutes" and z <= -2.0 and med - x >= floor * 2 and day != today:
                reasons.append("a lot less screen time than usual (nice break?)")
        if reasons:
            out.append({"day": day, "score": round(max(zmax, iso.get(day, 0.0)), 2), "reasons": reasons})
    return sorted(out, key=lambda a: a["day"], reverse=True)


def _isolation_scores(daily: dict[str, dict]) -> dict[str, float]:
    """Optional multivariate view via scikit-learn's IsolationForest."""
    if len(daily) < 10:
        return {}
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        return {}
    days = sorted(daily)
    X = np.array([[daily[d][f] for f in FEATURES] for d in days], dtype=float)
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-6)
    forest = IsolationForest(n_estimators=150, random_state=0, contamination="auto").fit(X)
    scores = -forest.score_samples(X)          # higher = more anomalous, ~0.35..0.75
    scale = (scores - np.median(scores)) / (scores.std() + 1e-6)
    return {d: round(float(s), 2) for d, s in zip(days, scale)}


def first_seen_days(visits: list[dict]) -> dict[str, str]:
    seen: dict[str, str] = {}
    for v in visits:
        site = registrable(v["domain"])
        if site and (site not in seen or v["day"] < seen[site]):
            seen[site] = v["day"]
    return seen
