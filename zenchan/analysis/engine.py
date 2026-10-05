"""Run the full analysis and cache one insight document per time window."""

from __future__ import annotations

import time
from collections import Counter, defaultdict

from .. import knowledge as K
from ..timeutil import is_late, local, now_local, window_bounds
from . import anomalies, habits, identity, patterns, sessions, topics
from .categorize import categorize_pending
from .embed import available_backends, get_embedder
from .text import is_worry_search, registrable, tokens

WINDOWS = ["today", "7d", "30d", "all"]
VISIT_COLUMNS = ("id, ts, day, hour, weekday, url, domain, title, title_key, category, duration, device, "
                 "feed, transition, search_query, valence, emotion, source_id, mobile_url")


def load_visits(db, since: float = 0.0) -> list[dict]:
    rows = db.q(f"SELECT {VISIT_COLUMNS} FROM visits WHERE ts >= ? ORDER BY device, ts", (since,))
    return [dict(r) for r in rows]


def device_label(device: str, aliases: dict) -> str:
    if device in aliases:
        return aliases[device]
    if device == "this-device":
        return "This computer"
    if device.startswith("chrome-sync:"):
        return f"Chrome-synced device {device.split(':', 1)[1]}"
    if device == "icloud":
        return "iCloud device (iPhone/iPad)"
    if device == "firefox-sync":
        return "Firefox Sync device"
    if device.startswith("takeout:"):
        return f"Takeout device {device.split(':', 1)[1]}"
    if device.startswith("ext:"):
        return device.split(":", 1)[1]
    return device


def device_kind(device: str, label: str, mobile_share: float) -> str:
    low = label.lower()
    if device == "this-device":
        return "computer"
    if mobile_share >= 0.2 or any(w in low for w in ("phone", "android", "pixel", "iphone", "galaxy", "mobile")):
        return "phone"
    if "ipad" in low or "tablet" in low or device == "icloud":
        return "tablet"
    return "device"


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def intentionality(total: float, productive: float, focus: float, doom: float, late: float,
                   switch_rate: float, overrun: float) -> dict | None:
    if total < 5:
        return None
    parts = [
        ("Intentional time", 40 * productive / total),
        ("Deep focus", 10 * clamp(focus / total * 2)),
        ("Doomscrolling", -25 * clamp(doom / total * 2)),
        ("Past bedtime", -15 * clamp(late / total * 3)),
        ("Fragmentation", -10 * clamp((switch_rate - 25) / 60)),
        ("Over your limits", -10 * clamp(overrun)),
    ]
    value = round(clamp(50 + sum(p for _, p in parts), 0, 100))
    label = ("Intentional" if value >= 75 else "Mostly intentional" if value >= 55
             else "Drifting" if value >= 35 else "On autopilot")
    return {"value": value, "label": label,
            "components": [{"name": n, "points": round(p, 1)} for n, p in parts if abs(p) >= 0.5]}


def analyze(zen, progress=None, with_topics: bool = True) -> dict:
    emit = progress or (lambda _m: None)
    t0 = time.time()
    db, settings = zen.db, zen.settings
    embedder = get_embedder(settings["ml"].get("embedder", "auto"))
    emit(f"> ML backend: {embedder.name}{' (semantic)' if embedder.semantic else ' (lexical fallback)'}")
    categorize_pending(zen, embedder, emit)
    visits = load_visits(db)
    if not visits:
        for w in WINDOWS:
            db.put_insight(w, {"window": w, "empty": True})
        db.set_meta("last_analysis", {"ts": time.time(), "visits": 0})
        emit("> no history yet")
        return {"visits": 0}
    emit(f"> {len(visits)} visits loaded; building sessions")
    sess = sessions.build(zen, visits, emit)
    topic_info = {}
    if with_topics:
        try:
            topic_info = topics.build(zen, embedder, emit)
        except Exception as exc:  # the map is a nice-to-have; never block insights on it
            emit(f">   topic map skipped: {type(exc).__name__}: {exc}")
    emit("> computing insights")
    db.set_meta("forecast_table", habits.forecast_table([v for v in visits if v["ts"] >= time.time() - 60 * 86400]))
    ctx = _context(zen, visits, sess)
    for w in WINDOWS:
        db.put_insight(w, compute_window(ctx, w))
    took = round(time.time() - t0, 1)
    db.set_meta("last_analysis", {"ts": time.time(), "visits": len(visits), "sessions": len(sess),
                                  "seconds": took, "embedder": embedder.name, **topic_info})
    emit(f"> analysis done in {took}s")
    return {"visits": len(visits), "sessions": len(sess), "seconds": took, **topic_info}


def _context(zen, visits: list[dict], sess: list[dict]) -> dict:
    db, settings, tz = zen.db, zen.settings, zen.tz
    aliases = settings.get("device_aliases", {})
    source_labels = {r["id"]: f"{r['browser']} · {r['name']}" for r in db.q("SELECT id, browser, name FROM sources")}
    for v in visits:
        if v["source_id"].startswith("live:"):
            source_labels.setdefault(v["source_id"], f"Live ({v['source_id'][5:]})")
    topic_of = {r["key"]: r["topic"] for r in db.q("SELECT key, topic FROM titles WHERE topic IS NOT NULL")}
    topic_rows = {r["id"]: dict(r) for r in db.q("SELECT * FROM topics")}
    first_seen = anomalies.first_seen_days(visits)
    goals = settings["goals"]
    return {
        "zen": zen, "tz": tz, "goals": goals, "settings": settings, "visits": visits, "sessions": sess,
        "aliases": aliases, "source_labels": source_labels, "topic_of": topic_of, "topics": topic_rows,
        "first_seen": first_seen, "daily": anomalies.daily_table(visits, goals, first_seen, tz),
        "regret_model": db.get_meta("regret_model"), "now": now_local(tz),
    }


def compute_window(ctx: dict, window: str) -> dict:
    tz, goals, settings = ctx["tz"], ctx["goals"], ctx["settings"]
    start, end = window_bounds(window, tz, ctx["now"])
    vs = [v for v in ctx["visits"] if start <= v["ts"] <= end + 60]
    if not vs:
        return {"window": window, "empty": True, "start": start, "end": end}
    productive_cats = set(goals.get("productive", []))
    by_session: dict[int, list] = defaultdict(list)
    for v in vs:
        by_session[v.get("session_id") or 0].append(v)
    sess_by_id = {s["id"]: s for s in ctx["sessions"]}
    sessions_visits = [sorted(g, key=lambda v: v["ts"]) for g in by_session.values()]
    days = sorted({v["day"] for v in vs})
    n_days = max(len(days), 1)

    total_min = sum((v["duration"] or 0) for v in vs) / 60
    cm = identity.category_minutes(vs)
    cat_visits = Counter(v["category"] or "Other" for v in vs)
    categories = [{"name": c, "minutes": round(m, 1), "share": round(m / total_min, 4) if total_min else 0,
                   "visits": cat_visits[c], "color": K.color_of(c),
                   "kind": "productive" if c in productive_cats else K.kind_of(c)}
                  for c, m in cm.most_common()]

    dom_min, dom_vis, dom_cat, dom_feed = Counter(), Counter(), defaultdict(Counter), {}
    for v in vs:
        dom_min[v["domain"]] += (v["duration"] or 0) / 60
        dom_vis[v["domain"]] += 1
        dom_cat[v["domain"]][v["category"] or "Other"] += 1
        dom_feed[v["domain"]] = bool(v["feed"])
    domains = [{"domain": d, "minutes": round(m, 1), "visits": dom_vis[d],
                "category": dom_cat[d].most_common(1)[0][0], "feed": dom_feed[d]}
               for d, m in dom_min.most_common(15)]

    # devices & browsers
    dev = defaultdict(lambda: {"minutes": 0.0, "visits": 0, "mobile": 0, "sources": Counter()})
    src = defaultdict(lambda: {"minutes": 0.0, "visits": 0, "cats": Counter()})
    for v in vs:
        d = dev[v["device"]]
        d["minutes"] += (v["duration"] or 0) / 60
        d["visits"] += 1
        d["mobile"] += v["mobile_url"] or 0
        d["sources"][ctx["source_labels"].get(v["source_id"], v["source_id"]).split(" · ")[0]] += 1
        s = src[v["source_id"]]
        s["minutes"] += (v["duration"] or 0) / 60
        s["visits"] += 1
        s["cats"][v["category"] or "Other"] += v["duration"] or 0
    devices = []
    for key, d in sorted(dev.items(), key=lambda kv: -kv[1]["minutes"]):
        label = device_label(key, ctx["aliases"])
        devices.append({"device": key, "label": label, "kind": device_kind(key, label, d["mobile"] / d["visits"]),
                        "minutes": round(d["minutes"], 1), "visits": d["visits"],
                        "via": [b for b, _ in d["sources"].most_common(3)]})
    browsers = [{"source": key, "label": ctx["source_labels"].get(key, key), "minutes": round(s["minutes"], 1),
                 "visits": s["visits"], "top": s["cats"].most_common(1)[0][0]}
                for key, s in sorted(src.items(), key=lambda kv: -kv[1]["minutes"])]

    # sessions/states/mood
    win_sessions = [sess_by_id[sid] for sid in by_session if sid in sess_by_id]
    state_min, state_n = Counter(), Counter()
    for sid, group in by_session.items():
        s = sess_by_id.get(sid)
        if s:
            state_min[s["state"]] += sum((v["duration"] or 0) for v in group) / 60
            state_n[s["state"]] += 1
    states = [{"state": st, "minutes": round(m, 1), "sessions": state_n[st], "color": sessions.STATES[st]["color"],
               "desc": sessions.STATES[st]["desc"]} for st, m in state_min.most_common()]
    weight = sum(s["active_sec"] for s in win_sessions) or 1.0
    valence = sum(s["valence"] * s["active_sec"] for s in win_sessions) / weight
    arousal = sum(s["arousal"] * s["active_sec"] for s in win_sessions) / weight
    mood_days = defaultdict(lambda: [0.0, 0.0, 0.0])
    for s in win_sessions:
        md = mood_days[s["day"]]
        md[0] += s["valence"] * s["active_sec"]
        md[1] += s["arousal"] * s["active_sec"]
        md[2] += s["active_sec"]
    mood_timeline = [{"day": d, "valence": round(a / w, 3), "arousal": round(b / w, 3)}
                     for d, (a, b, w) in sorted(mood_days.items()) if w > 0]

    # patterns
    nudge_cfg = settings["nudges"]
    doom = patterns.doomscroll_episodes(sessions_visits, nudge_cfg.get("doomscroll_minutes", 20), tz, goals)
    holes = patterns.rabbit_holes(sessions_visits, tz)
    checks = patterns.compulsive_checks(vs, n_days, min_per_day=6)
    spirals = patterns.search_spirals(sessions_visits, nudge_cfg.get("search_spiral", 4), tz)
    switch = patterns.switching(vs)
    nights = patterns.late_nights(vs, goals, tz)
    news_social = [v for v in vs if v["category"] in ("News & Politics", "Social Media")]
    ns_total = sum((v["duration"] or 0) for v in news_social)
    outrage = (sum((v["duration"] or 0) for v in news_social if v["emotion"] in ("outrage", "fear")) / ns_total
               if ns_total else 0.0)
    queries = [v for v in vs if v["search_query"]]
    worry = [v["search_query"] for v in queries if is_worry_search(v["search_query"])]

    # score
    productive_min = sum(m for c, m in cm.items() if c in productive_cats)
    focus_min = state_min.get("Deep Focus", 0.0)
    doom_min = sum(e["minutes"] for e in doom)
    late_min = nights["minutes"]
    limits = goals.get("limits", {})
    overruns = [max(0.0, cm.get(c, 0) / n_days - lim) / lim for c, lim in limits.items() if lim]
    overrun = sum(overruns) / len(overruns) if overruns else 0.0
    score = intentionality(total_min, productive_min, focus_min, doom_min, late_min, switch["per_hour"], overrun)

    by_day: dict[str, list] = defaultdict(list)
    for v in vs:
        by_day[v["day"]].append(v)
    daily = []
    for day in days[-60:]:
        dvs = by_day[day]
        dcm = identity.category_minutes(dvs)
        dmin = sum(dcm.values())
        dlate = sum((v["duration"] or 0) for v in dvs if is_late(local(v["ts"], tz), goals.get("bedtime", "23:30"),
                                                                 goals.get("wake", "06:30"))) / 60
        dfeed = sum((v["duration"] or 0) for v in dvs if v["feed"]) / 60
        dprod = sum(m for c, m in dcm.items() if c in productive_cats)
        dsw = patterns.switching(dvs)["per_hour"]
        dscore = intentionality(dmin, dprod, 0.0, min(dfeed, dmin), dlate, dsw, 0.0)
        daily.append({"day": day, "minutes": round(dmin, 1), "late": round(dlate, 1), "feed": round(dfeed, 1),
                      "productive": round(dprod, 1), "score": dscore["value"] if dscore else None,
                      "top": dcm.most_common(1)[0][0] if dcm else None})

    # identity
    feed_share = sum((v["duration"] or 0) for v in vs if v["feed"]) / 60 / total_min if total_min else 0.0
    late_share = late_min / total_min if total_min else 0.0
    div = identity.diversity(cm)
    arche = identity.archetypes(vs, feed_share, late_share, div)
    chrono = identity.chronotype(vs)
    topic_min = Counter()
    for v in vs:
        t = ctx["topic_of"].get(v["title_key"])
        if t is not None:
            topic_min[t] += (v["duration"] or 0) / 60
    interests = []
    for t, m in topic_min.most_common(10):
        row = ctx["topics"].get(t)
        if row:
            interests.append({"topic": t, "label": row["label"], "keywords": (row["keywords"] or "").split(","),
                              "minutes": round(m, 1), "share": round(m / total_min, 3) if total_min else 0,
                              "category": row["category"]})
    ident = {
        "headline": (identity.headline(arche, chrono) if total_min >= 120
                     else "Too early to tell — check the 7-day view"),
        "archetypes": arche[:6], "chronotype": chrono, "diversity": div,
        "novelty": round(sum(1 for s, d in ctx["first_seen"].items() if d in set(days)) /
                         max(len({registrable(v["domain"]) for v in vs}), 1), 3),
        "interests": interests,
        "drift": identity.drift(ctx["visits"], ctx["now"].timestamp()),
        "ad_profile": identity.ad_profile(vs),
        "per_source": identity.per_source(vs, {**ctx["source_labels"],
                                               **{d["device"]: d["label"] for d in devices}}),
    }

    term_counts = Counter()
    for v in queries:
        term_counts.update(set(tokens(v["search_query"])))
    recent_q = []
    for v in sorted(queries, key=lambda v: -v["ts"]):
        if v["search_query"] not in recent_q:
            recent_q.append(v["search_query"])
        if len(recent_q) >= 15:
            break

    check_days = days if window != "all" else days[-30:]
    found_anomalies = anomalies.detect(ctx["daily"], check_days, today=ctx["now"].strftime("%Y-%m-%d"))

    now = ctx["now"]
    recent_sessions = sorted(win_sessions, key=lambda s: -s["start"])[:40]
    result = {
        "window": window, "start": start, "end": end, "generated": time.time(),
        "totals": {"minutes": round(total_min, 1), "visits": len(vs), "sites": len({registrable(v["domain"]) for v in vs}),
                   "sessions": len(win_sessions), "days": n_days, "per_day": round(total_min / n_days, 1),
                   "searches": len(queries), "productive_minutes": round(productive_min, 1)},
        "score": score,
        "categories": categories, "domains": domains, "devices": devices, "browsers": browsers,
        "heatmap": habits.heatmap(vs), "daily": daily,
        "states": states,
        "mood": {"valence": round(valence, 3), "arousal": round(arousal, 3),
                 "label": sessions.mood_label(valence, arousal), "timeline": mood_timeline},
        "patterns": {"doomscroll": doom[:12], "doom_minutes": round(doom_min, 1), "rabbit_holes": holes[:8],
                     "compulsive": checks, "search_spirals": spirals[:6], "switching": switch,
                     "late": nights, "worry_searches": len(worry), "worry_examples": list(dict.fromkeys(worry))[:5],
                     "outrage_share": round(outrage, 3)},
        "habits": {**habits.transitions(sessions_visits), "rituals": habits.rituals(vs, n_days, tz),
                   "forecast": habits.forecast(ctx["visits"], now.weekday(), now.hour)},
        "identity": ident,
        "anomalies": found_anomalies[:10],
        "searches": {"top_terms": [{"term": t, "n": n} for t, n in term_counts.most_common(20)],
                     "recent": recent_q},
        "sessions": [_session_brief(s, ctx) for s in recent_sessions],
        "regret_model": ctx["regret_model"],
        "ml": {"backends": available_backends()},
    }
    from ..buddy.narrator import local_summary
    result["buddy"] = local_summary(result, settings)
    return result


def _session_brief(s: dict, ctx: dict) -> dict:
    dt = local(s["start"], ctx["tz"])
    return {"id": s["id"], "when": dt.strftime("%a %d %b %H:%M"), "start": s["start"],
            "minutes": round(s["active_sec"] / 60, 1), "state": s["state"],
            "color": sessions.STATES[s["state"]]["color"], "top_domain": s["top_domain"],
            "top_category": s["top_category"], "visits": s["visits"], "regret": s["regret"],
            "label": s["label"], "device": device_label(s["device"], ctx["aliases"]),
            "mood": sessions.mood_label(s["valence"], s["arousal"])}
