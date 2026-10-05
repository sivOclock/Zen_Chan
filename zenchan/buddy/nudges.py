"""The watcher's judgement: what's happening right now, and is it worth a nudge?"""

from __future__ import annotations

import json
import time
from collections import Counter

import numpy as np

from .. import knowledge as K
from ..analysis import sessions as S
from ..analysis.engine import load_visits
from ..analysis.patterns import compulsive_checks
from ..analysis.text import is_worry_search, registrable, tokens
from ..timeutil import day_start, fmt_minutes, is_late, local, now_local

SEVERITY_RANK = {"care": 5, "warn": 4, "good": 3, "info": 2}


def now_status(zen, now: float | None = None) -> dict:
    """A live snapshot: what you're doing, for how long, in what state."""
    db, tz, settings = zen.db, zen.tz, zen.settings
    now = now or time.time()
    goals = settings["goals"]
    productive = set(goals.get("productive", []))
    today0 = day_start(local(now, tz)).timestamp()
    today_rows = db.q("SELECT category, SUM(duration) s FROM visits WHERE ts >= ? GROUP BY category", (today0,))
    today = {r["category"] or "Other": (r["s"] or 0) / 60 for r in today_rows}
    base = {"active": False, "ts": now, "today_minutes": round(sum(today.values()), 1),
            "today": {k: round(v, 1) for k, v in sorted(today.items(), key=lambda kv: -kv[1])[:6]},
            "face": K.FACES["sleepy"], "line": "Nothing happening right now."}
    visits = [v for v in load_visits(db, min(now - 4 * 3600, today0)) if v["ts"] <= now]
    if not visits:
        return base
    latest = max(visits, key=lambda v: v["ts"])
    idle_for = now - latest["ts"]
    long_dwell = bool(K.LONG_DWELL_URL.search(latest["url"] or ""))
    active = idle_for < (3 * 3600 if long_dwell else 15 * 60)
    device_visits = sorted((v for v in visits if v["device"] == latest["device"]), key=lambda v: v["ts"])
    sitting = S.split(device_visits)[-1]
    episode = S.episodes(sitting, productive)[-1]
    f = S.features(episode, goals, tz)
    scores = S.score_states(f, settings["nudges"].get("doomscroll_minutes", 20))
    state = S.pick_state(scores)
    model = S.load_regret(db)
    prior = S.regret_prior(f)
    regret = prior if model is None else float(0.75 * model.predict(np.array([S.vector(f)]))[0] + 0.25 * prior)
    meta = S.STATES[state]
    episode_minutes = (now - episode[0]["ts"]) / 60 if active else f["minutes"]
    if active:
        line = f"{state} · {fmt_minutes(episode_minutes)} · on {latest['domain']}"
    else:
        line = f"Away for {fmt_minutes(idle_for / 60)}. Last: {state.lower()} on {latest['domain']}."
    return {
        **base, "active": active, "idle_seconds": round(idle_for), "device": latest["device"],
        "domain": latest["domain"], "title": latest["title"], "category": latest["category"],
        "state": state, "state_color": meta["color"], "state_desc": meta["desc"],
        "episode_minutes": round(episode_minutes, 1), "sitting_minutes": round((now - sitting[0]["ts"]) / 60, 1),
        "regret": round(regret, 3), "regret_personal": model is not None,
        "face": K.FACES[meta["face"]] if active else K.FACES["sleepy"], "line": line,
        "_episode": episode, "_features": f, "_visits": visits,
    }


def public(status: dict) -> dict:
    return {k: v for k, v in status.items() if not k.startswith("_")}


def _late_nights_this_week(db, tz, goals, now: float) -> int:
    rows = db.q("SELECT ts FROM visits WHERE ts >= ?", (now - 7 * 86400,))
    nights = set()
    for r in rows:
        dt = local(r["ts"], tz)
        if is_late(dt, goals.get("bedtime", "23:30"), goals.get("wake", "06:30")):
            nights.add((dt.toordinal() - (1 if dt.hour < 12 else 0)))
    return len(nights)


def candidates(zen, status: dict, now: float | None = None) -> list[dict]:
    """All nudges that *could* fire now (dedup keys decide which actually do)."""
    if not status.get("active"):
        return []
    now = now or time.time()
    settings, db, tz = zen.settings, zen.db, zen.tz
    cfg, goals = settings["nudges"], settings["goals"]
    ep, f, visits = status["_episode"], status["_features"], status["_visits"]
    sess_key = f"{status['device']}:{int(ep[0]['ts'])}"
    nowdt = local(now, tz)
    day = nowdt.strftime("%Y-%m-%d")
    out = []

    def add(kind, severity, title, body, dedup, **data):
        out.append({"kind": kind, "severity": severity, "title": title, "body": body, "dedup": dedup, "data": data})

    thr = cfg.get("doomscroll_minutes", 20)
    run_min, i, j = S.longest_feed_run(ep)
    if run_min >= thr and j >= len(ep) - 3:
        run = ep[i:j + 1]
        sites = ", ".join(d for d, _ in Counter(v["domain"] for v in run if v["feed"]).most_common(2))
        per_item = round(run_min * 60 / max(len(run), 1))
        add("doomscroll", "warn", f"{K.FACES['worried']} Doomscroll check",
            f"{fmt_minutes(run_min)} on {sites}, ~{per_item}s per post. Still choosing this, or is it choosing you?",
            f"doom:{sess_key}:{int(run_min // thr)}", minutes=round(run_min, 1))

    if is_late(nowdt, goals.get("bedtime", "23:30"), goals.get("wake", "06:30")):
        night = (nowdt.toordinal() - (1 if nowdt.hour < 12 else 0))
        n = _late_nights_this_week(db, tz, goals, now)
        add("late", "warn", f"{K.FACES['sleepy']} It's {nowdt.strftime('%H:%M')}",
            f"Past your {goals.get('bedtime', '23:30')} bedtime — late night #{max(n, 1)} this week. "
            "Tomorrow-you would like some sleep.", f"late:{night}", nights=n)

    binge = cfg.get("binge_minutes", 90)
    if f["video_share"] >= 0.6 and f["minutes"] >= binge:
        add("binge", "info", f"{K.FACES['calm']} Long watch",
            f"{fmt_minutes(f['minutes'])} of watching in one go. A stretch and some water?",
            f"binge:{sess_key}:{int(f['minutes'] // binge)}")

    recent = sorted((v for v in visits if v["ts"] >= now - 1800), key=lambda v: v["ts"])
    switches = sum(1 for a, b in zip(recent, recent[1:]) if a["domain"] != b["domain"])
    if switches >= cfg.get("switches_per_30min", 45):
        add("fragmented", "info", f"{K.FACES['alert']} Scattered",
            f"{switches} site switches in 30 minutes. Pick one thing for the next 25?",
            f"frag:{int(now // 1800)}", switches=switches)

    today0 = day_start(nowdt).timestamp()
    today_visits = [v for v in visits if v["ts"] >= today0]
    site = registrable(status["domain"] or "")
    min_checks = cfg.get("compulsive_checks", 10)
    for c in compulsive_checks([v for v in today_visits if registrable(v["domain"]) == site], 1, min_per_day=min_checks):
        add("checking", "info", f"{K.FACES['curious']} Checking again?",
            f"That's {c['checks']} visits to {site} today, ~{c['avg_minutes']:g} min each. What are you hoping to find?",
            f"checks:{day}:{site}:{c['checks'] // min_checks}", checks=c["checks"])

    queries = [v["search_query"] for v in recent if v["search_query"]]
    if len(queries) >= cfg.get("search_spiral", 4):
        common = Counter(t for q in queries for t in set(tokens(q)))
        top, n = common.most_common(1)[0] if common else ("", 0)
        if n >= cfg.get("search_spiral", 4):
            add("spiral", "info", f"{K.FACES['curious']} Going in circles?",
                f"{n} searches about “{top}” in 30 minutes. Maybe write down the actual question, or ask a person?",
                f"spiral:{day}:{top}")

    worry = [v["search_query"] for v in today_visits if is_worry_search(v["search_query"])]
    if len(worry) >= 3:
        add("worry", "care", f"{K.FACES['worried']} Hey, I noticed something",
            f"{len(worry)} worried-sounding searches today. Searching rarely settles worry — if it's been on your "
            "mind, talking to someone you trust (or a doctor) usually helps more.", f"worry:{day}", count=len(worry))

    limits = goals.get("limits", {})
    cat = status.get("category")
    if cat in limits:
        spent = sum((v["duration"] or 0) for v in today_visits if v["category"] == cat) / 60
        if spent >= limits[cat]:
            add("limit", "warn", f"{K.FACES['alert']} {cat} limit reached",
                f"{fmt_minutes(spent)} today vs. your {fmt_minutes(limits[cat])} limit.", f"limit:{day}:{cat}")

    if status["state"] == "Deep Focus" and f["minutes"] >= cfg.get("focus_praise_minutes", 50):
        add("focus", "good", f"{K.FACES['proud']} Nice focus",
            f"{fmt_minutes(f['minutes'])} of deep focus. Take a 5-minute break — your brain will thank you.",
            f"focus:{sess_key}")

    if status.get("regret_personal") and status["regret"] >= cfg.get("regret_threshold", 0.75) and f["minutes"] >= 15:
        add("regret", "warn", f"{K.FACES['worried']} This one looks familiar",
            f"This session looks like ones you've told me you regret ({status['regret']:.0%} match). Want to switch?",
            f"regret:{sess_key}")

    if cfg.get("forecast", True) and nowdt.minute < 10 and status["state"] in ("Deep Focus", "Learning"):
        table = db.get_meta("forecast_table") or {}
        row = table.get(f"{'weekend' if nowdt.weekday() >= 5 else 'weekday'}:{nowdt.hour}") or []
        leisure = [r for r in row if r["category"] in K.LEISURE]
        if leisure and leisure[0]["share"] >= 0.45:
            add("forecast", "info", f"{K.FACES['curious']} Heads-up",
                f"Around this hour you usually drift to {leisure[0]['category']} ({leisure[0]['share']:.0%}). "
                "You're in a good flow — want to keep it?", f"forecast:{day}:{nowdt.hour}")
    return out


def evaluate(zen, status: dict | None = None, now: float | None = None) -> list[dict]:
    """Store at most one new nudge per tick, respecting dedup keys and the cooldown."""
    settings, db = zen.settings, zen.db
    cfg = settings["nudges"]
    if not cfg.get("enabled", True):
        return []
    now = now or time.time()
    status = status or now_status(zen, now)
    cands = candidates(zen, status, now)
    if not cands:
        return []
    last = db.scalar("SELECT MAX(ts) FROM nudges", default=0.0)
    cooling = now - last < cfg.get("cooldown_minutes", 25) * 60
    cands.sort(key=lambda c: -SEVERITY_RANK[c["severity"]])
    for c in cands:
        if db.one("SELECT 1 FROM nudges WHERE dedup=?", (c["dedup"],)):
            continue
        if cooling and c["severity"] != "care":
            continue
        cur = db.x("""INSERT OR IGNORE INTO nudges(ts, kind, severity, title, body, data, dedup)
                      VALUES (?,?,?,?,?,?,?)""",
                   (now, c["kind"], c["severity"], c["title"], c["body"], json.dumps(c["data"]), c["dedup"]))
        if cur.rowcount:
            return [{**c, "id": cur.lastrowid, "ts": now}]
    return []


def recent(db, since: float = 0.0, limit: int = 50) -> list[dict]:
    rows = db.q("SELECT * FROM nudges WHERE ts > ? ORDER BY ts DESC LIMIT ?", (since, limit))
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d["data"] or "{}")
        out.append(d)
    return out
