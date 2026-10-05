"""Browsing identity: who your history says you are — and who else can tell."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from .. import knowledge as K
from ..timeutil import is_late, local
from .text import brand, registrable


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def category_minutes(visits: list[dict]) -> Counter:
    c: Counter = Counter()
    for v in visits:
        c[v["category"] or "Other"] += (v["duration"] or 0) / 60
    return c


def shares(cm: Counter) -> dict[str, float]:
    total = sum(cm.values()) or 1.0
    return {k: v / total for k, v in cm.items()}


ARCHETYPES = [
    # name, categories, share that counts as "fully" this archetype, blurb
    ("Builder", ["Programming & Dev", "AI Tools", "Work & Productivity"], 0.40, "makes things"),
    ("Scholar", ["Learning & Education", "Research & Reference", "Science"], 0.25, "learns for its own sake"),
    ("Binge-Watcher", ["Video & Streaming", "Entertainment & Pop Culture"], 0.30, "lives in the watch queue"),
    ("Gamer", ["Gaming"], 0.15, "plays and theorycrafts"),
    ("News Hound", ["News & Politics"], 0.12, "needs to know what's happening"),
    ("Shopper", ["Shopping"], 0.08, "researches before buying (or after)"),
    ("Investor", ["Finance & Crypto"], 0.08, "watches the markets"),
    ("Connector", ["Communication"], 0.10, "keeps the inbox and chats moving"),
    ("Creator", ["Art & Design", "Music & Audio"], 0.12, "chases aesthetics and sound"),
    ("Wanderer", ["Travel & Maps"], 0.06, "is always planning the next trip"),
    ("Career Climber", ["Jobs & Career"], 0.05, "is eyeing the next move"),
    ("Wellness Seeker", ["Health & Fitness", "Food & Cooking"], 0.10, "tends body and kitchen"),
    ("Fan", ["Sports"], 0.08, "follows every score"),
]


def archetypes(visits: list[dict], feed_share: float, late_share: float, diversity: float) -> list[dict]:
    """Interest archetypes (what you browse) and behaviour archetypes (how you browse).

    ``strength`` is the unclamped ratio to the archetype's reference share and is
    what we rank by; ``score`` is the same thing clamped to 0..1 for display.
    """
    sh = shares(category_minutes(visits))
    out = []

    def add(name, kind, strength, blurb, reason):
        out.append({"name": name, "kind": kind, "strength": round(strength, 3),
                    "score": round(clamp(strength), 3), "blurb": blurb, "reason": reason})

    for name, cats, full, blurb in ARCHETYPES:
        s = sum(sh.get(c, 0.0) for c in cats)
        add(name, "interest", s / full, blurb,
            f"{s:.0%} of your time on {', '.join(c.split(' &')[0].lower() for c in cats)}")
    add("Scroller", "behavior", feed_share / 0.25, "feeds on the feed",
        f"{feed_share:.0%} of your time in infinite feeds")
    add("Night Owl", "behavior", late_share / 0.15, "comes alive after bedtime",
        f"{late_share:.0%} of your browsing after your bedtime")
    add("Explorer", "behavior", max(0.0, (diversity - 0.72) / 0.2), "can't stay on one thing",
        f"interest spread {diversity:.0%} of maximum")
    out.sort(key=lambda a: -a["strength"])
    return out


def chronotype(visits: list[dict]) -> dict:
    hours = [0.0] * 24
    for v in visits:
        hours[v["hour"]] += (v["duration"] or 0) / 60
    total = sum(hours) or 1.0
    # circular mean of activity (so 23:00 and 01:00 average to midnight, not noon)
    sx = sum(m * math.cos(2 * math.pi * h / 24) for h, m in enumerate(hours))
    sy = sum(m * math.sin(2 * math.pi * h / 24) for h, m in enumerate(hours))
    center = (math.degrees(math.atan2(sy, sx)) / 15) % 24
    peak = max(range(24), key=lambda h: hours[h])
    if center < 4 or center >= 21:
        label = "Night Owl"
    elif center < 11:
        label = "Early Bird"
    elif center < 16:
        label = "Daytimer"
    else:
        label = "Evening Person"
    return {"label": label, "center": round(center, 2), "peak_hour": peak,
            "hours": [round(h / total, 4) for h in hours]}


def diversity(cm: Counter) -> float:
    total = sum(cm.values())
    if not total or len(cm) < 2:
        return 0.0
    ent = -sum((m / total) * math.log(m / total) for m in cm.values() if m > 0)
    return round(ent / math.log(len(K.CATEGORIES)), 3)


def drift(visits: list[dict], now_ts: float) -> dict:
    """How this week's interests differ from the previous four weeks."""
    week, base = Counter(), Counter()
    for v in visits:
        age = (now_ts - v["ts"]) / 86400
        target = week if age <= 7 else base if age <= 35 else None
        if target is not None:
            target[v["category"] or "Other"] += (v["duration"] or 0) / 60
    if sum(week.values()) < 30 or sum(base.values()) < 60:
        return {"similarity": None, "risers": [], "fallers": []}
    sw, sb = shares(week), shares(base)
    cats = set(sw) | set(sb)
    dot = sum(sw.get(c, 0) * sb.get(c, 0) for c in cats)
    norm = math.sqrt(sum(x * x for x in sw.values())) * math.sqrt(sum(x * x for x in sb.values()))
    deltas = sorted(((c, sw.get(c, 0) - sb.get(c, 0)) for c in cats), key=lambda kv: kv[1])
    risers = [{"category": c, "delta": round(d, 3)} for c, d in reversed(deltas) if d >= 0.02][:4]
    fallers = [{"category": c, "delta": round(d, 3)} for c, d in deltas if d <= -0.02][:4]
    return {"similarity": round(dot / norm, 3) if norm else None, "risers": risers, "fallers": fallers}


def _domain_hit(v: dict, domains: list[str]) -> bool:
    dom, url = v["domain"] or "", v["url"] or ""
    for d in domains:
        if "/" in d:
            if d in url:
                return True
        elif dom == d or dom.endswith("." + d):
            return True
    return False


def ad_profile(visits: list[dict]) -> dict:
    """What an ad-tech data broker could plausibly infer from this history."""
    segments = []
    for seg in K.SEGMENTS:
        hits, days, strong, weak = 0, set(), [], []
        for v in visits:
            text = v["search_query"] or v["title"] or ""
            evidence = None
            if _domain_hit(v, seg["domains"]) or (v["search_query"] and seg["re"].search(v["search_query"])):
                evidence = strong
            elif v["category"] not in ("Social Media", "Video & Streaming") and seg["type"] != "commercial" \
                    and seg["re"].search(v["title"] or ""):
                evidence = weak
            if evidence is not None:
                hits += 1
                days.add(v["day"])
                if len(evidence) < 3 and text and text[:90] not in evidence:
                    evidence.append(text[:90])
        if hits >= 3 and len(days) >= 2:
            segments.append({"segment": seg["name"], "type": seg["type"], "signals": hits, "days": len(days),
                             "confidence": round(clamp(0.25 + 0.08 * len(days) + 0.01 * hits), 2),
                             "examples": (strong + weak)[:3]})
    segments.sort(key=lambda s: (-s["confidence"], -s["signals"]))

    owners = {b: company for company, brands in K.PLATFORM_OWNERS.items() for b in brands}
    by_company: Counter = Counter()
    total = 0.0
    for v in visits:
        d = v["duration"] or 0
        total += d
        company = owners.get(brand(v["domain"] or ""))
        if company:
            by_company[company] += d
    platforms = [{"company": c, "share": round(s / total, 3)} for c, s in by_company.most_common(8)] if total else []
    return {"segments": segments, "platforms": platforms,
            "distinct_sites": len({registrable(v["domain"]) for v in visits if v["domain"]})}


def headline(arche: list[dict], chrono: dict) -> str:
    """'Night-Owl Builder with a Scroller streak'."""
    interests = [a for a in arche if a["kind"] == "interest" and a["strength"] >= 0.45]
    behaviors = {a["name"]: a for a in arche if a["kind"] == "behavior"}
    if not interests:
        return f"{chrono['label']} Generalist"
    main = interests[0]["name"]
    if behaviors["Night Owl"]["strength"] >= 0.7 or chrono["label"] == "Night Owl":
        adjective = "Night-Owl "
    elif chrono["label"] == "Early Bird":
        adjective = "Early-Bird "
    else:
        adjective = ""
    streaks = [a for a in arche if a["name"] not in (main, "Night Owl") and a["strength"] >= 0.6]
    return f"{adjective}{main}" + (f" with a {streaks[0]['name']} streak" if streaks else "")


def per_source(visits: list[dict], labels: dict[str, str]) -> list[dict]:
    """Each browser profile / device tends to have its own personality."""
    groups: defaultdict = defaultdict(list)
    for v in visits:
        groups[("source", v["source_id"])].append(v)
        groups[("device", v["device"])].append(v)
    out = []
    for (kind, key), vs in groups.items():
        cm = category_minutes(vs)
        total = sum(cm.values())
        if total < 20:
            continue
        sh = shares(cm)
        feed = sum((v["duration"] or 0) for v in vs if v["feed"]) / 60 / total
        arch = archetypes(vs, feed, 0.0, diversity(cm))[0]
        out.append({"kind": kind, "key": key, "label": labels.get(key, key), "minutes": round(total),
                    "persona": arch["name"], "top": [{"category": c, "share": round(s, 3)}
                                                      for c, s in sorted(sh.items(), key=lambda kv: -kv[1])[:3]]})
    out.sort(key=lambda r: (r["kind"], -r["minutes"]))
    return out


def late_share(visits: list[dict], goals: dict, tz=None) -> float:
    total = sum((v["duration"] or 0) for v in visits) or 1.0
    late = sum((v["duration"] or 0) for v in visits
               if is_late(local(v["ts"], tz), goals.get("bedtime", "23:30"), goals.get("wake", "06:30")))
    return late / total
