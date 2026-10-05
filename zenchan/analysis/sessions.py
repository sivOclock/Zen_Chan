"""Sessions, browsing states, inferred mood, and the personal regret model.

A *session* is a run of browsing on one device without a 10-minute break.
Each session gets interpretable features (how fragmented, how much feed
content, how late, how negative the headlines were...) which drive:

* a **state** — Deep Focus, Learning, Exploring, Rabbit Hole, Doomscrolling,
  Restless, Escapism, Worry Spiral, Winding Down, Errands, Casual Browsing;
* an **inferred mood** on the valence/arousal circumplex (honest caveat: this
  is a reading of your browsing, not of your mind);
* a **regret probability** — a prior heuristic until you label a few sessions
  as "worth it" / "regret it", then a logistic-regression model trained on
  *your* labels.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter

import numpy as np

from .. import knowledge as K
from ..timeutil import is_late, local
from .text import is_worry_search, registrable

SESSION_BREAK = 10 * 60
MIN_EPISODE = 6 * 60
RABBIT_CATEGORIES = {"Research & Reference", "Reading & Blogs", "Entertainment & Pop Culture", "Science",
                     "Video & Streaming", "Learning & Education"}
VIDEOISH = {"Video & Streaming", "Entertainment & Pop Culture", "Gaming"}
LEARNING = {"Learning & Education", "Research & Reference", "Science"}
UTILITY = {"Shopping", "Finance & Crypto", "Travel & Maps", "Food & Cooking", "Communication", "Search"}

STATES: dict[str, dict] = {
    "Deep Focus": {"color": "#39ff14", "valence": 0.35, "arousal": 0.45, "face": "proud",
                   "desc": "long, low-switching stretches on what you call productive"},
    "Learning": {"color": "#adff2f", "valence": 0.35, "arousal": 0.4, "face": "curious",
                 "desc": "courses, references, explanations"},
    "Exploring": {"color": "#00e5ff", "valence": 0.25, "arousal": 0.55, "face": "curious",
                  "desc": "wide, curious hopping across many sites"},
    "Rabbit Hole": {"color": "#da70d6", "valence": 0.1, "arousal": 0.6, "face": "curious",
                    "desc": "link after link, drifting far from where you started"},
    "Errands": {"color": "#87ceeb", "valence": 0.05, "arousal": 0.35, "face": "calm",
                "desc": "shopping, banking, travel, mail — getting things done"},
    "Casual Browsing": {"color": "#5f8f5f", "valence": 0.05, "arousal": 0.3, "face": "calm",
                        "desc": "a bit of everything, nothing dominant"},
    "Winding Down": {"color": "#b0c4de", "valence": 0.15, "arousal": 0.15, "face": "sleepy",
                     "desc": "slow, low-intensity leisure late in the day"},
    "Escapism": {"color": "#ff7f50", "valence": -0.05, "arousal": 0.35, "face": "calm",
                 "desc": "long video, streaming or gaming stretches"},
    "Restless": {"color": "#ffb000", "valence": -0.2, "arousal": 0.8, "face": "alert",
                 "desc": "rapid switching, nothing held your attention"},
    "Doomscrolling": {"color": "#ff3366", "valence": -0.35, "arousal": 0.6, "face": "worried",
                      "desc": "long runs of infinite feeds, short dwell per item"},
    "Worry Spiral": {"color": "#ff4500", "valence": -0.55, "arousal": 0.75, "face": "worried",
                     "desc": "anxious searches and negative reading"},
}

FEATURES = ["log_minutes", "feed_share", "leisure_share", "productive_share", "switch_rate",
            "late_share", "dwell", "neg_share", "doom", "rabbit", "hour_sin", "hour_cos"]


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def split(visits: list[dict]) -> list[list[dict]]:
    """Visits must be sorted by (device, ts)."""
    sessions, cur = [], []
    for v in visits:
        if cur:
            prev = cur[-1]
            if v["device"] != prev["device"] or v["ts"] - (prev["ts"] + (prev["duration"] or 0)) > SESSION_BREAK:
                sessions.append(cur)
                cur = []
        cur.append(v)
    if cur:
        sessions.append(cur)
    return sessions


def mode(v: dict, productive: set) -> str | None:
    """Coarse attention mode of one page view; None = neutral (search pages)."""
    cat = v["category"] or "Other"
    if cat == "Search":
        return None
    if v["feed"]:
        return "feed"
    if cat in productive or cat in LEARNING:
        return "focus"
    if cat in VIDEOISH:
        return "watch"
    if cat in UTILITY:
        return "errand"
    return "other"


def episodes(vs: list[dict], productive: set) -> list[list[dict]]:
    """Split one sitting into episodes of a consistent attention mode.

    A five-minute Reddit check in the middle of an afternoon of work is an
    interruption *of* the work episode; a 30-minute scroll is its own episode.
    """
    runs: list[list] = []                     # [mode, visits, seconds]
    for v in vs:
        m, d = mode(v, productive), v["duration"] or 0
        if runs and (m is None or runs[-1][0] == m):
            runs[-1][1].append(v)
            runs[-1][2] += d
        else:
            runs.append([m or "other", [v], d])
    merged: list[list] = []
    for run in runs:
        if merged and (run[2] < MIN_EPISODE or merged[-1][0] == run[0]):
            merged[-1][1].extend(run[1])
            merged[-1][2] += run[2]
        elif merged and merged[-1][2] < MIN_EPISODE and len(merged) == 1:
            run[1][:0] = merged[-1][1]            # a short opener joins what follows
            run[2] += merged[-1][2]
            merged[-1] = run
        else:
            merged.append(run)
    return [r[1] for r in merged]


def longest_feed_run(vs: list[dict]) -> tuple[float, int, int]:
    """Longest contiguous feed run (minutes, start index, end index). Short detours (<60s) don't break it."""
    best, best_i, best_j = 0.0, 0, -1
    run, start, detour = 0.0, None, 0.0
    for i, v in enumerate(vs):
        d = v["duration"] or 0
        if v["feed"]:
            if start is None:
                start, run = i, 0.0
            run += d + detour
            detour = 0.0
            if run > best:
                best, best_i, best_j = run, start, i
        elif start is not None and d < 60 and detour < 120:
            detour += d
        else:
            start, run, detour = None, 0.0, 0.0
    return best / 60, best_i, best_j


def longest_chain(vs: list[dict]) -> tuple[int, int, int]:
    """Longest run of link-hops through distinct pages on one rabbit-hole-prone site."""
    best, best_i, best_j = 0, 0, -1
    start, length, titles = None, 0, set()
    for i, v in enumerate(vs):
        ok = (v["category"] in RABBIT_CATEGORIES and not v["feed"]
              and v["transition"] in ("link", "", "generated") and v["title"])
        same_site = start is not None and registrable(v["domain"]) == registrable(vs[i - 1]["domain"])
        if ok and same_site and v["title"] not in titles:
            length += 1
            titles.add(v["title"])
        elif ok:
            start, length, titles = i, 1, {v["title"]}
        else:
            start, length, titles = None, 0, set()
        if length > best:
            best, best_i, best_j = length, start, i
    return best, best_i, best_j


def features(vs: list[dict], goals: dict, tz=None) -> dict:
    total = sum((v["duration"] or 0) for v in vs) or 1.0
    cat_sec: Counter = Counter()
    for v in vs:
        cat_sec[v["category"] or "Other"] += v["duration"] or 0
    productive = set(goals.get("productive", []))
    share = lambda cats: sum(cat_sec[c] for c in cats) / total
    switches = sum(1 for a, b in zip(vs, vs[1:]) if a["domain"] != b["domain"])
    active_hr = total / 3600
    feed_sec = sum((v["duration"] or 0) for v in vs if v["feed"])
    doom_min, _, _ = longest_feed_run(vs)
    chain, _, _ = longest_chain(vs)
    bedtime, wake = goals.get("bedtime", "23:30"), goals.get("wake", "06:30")
    late_sec = sum((v["duration"] or 0) for v in vs if is_late(local(v["ts"], tz), bedtime, wake))
    neg_sec = sum((v["duration"] or 0) for v in vs if (v["valence"] or 0) < -0.2)
    outrage_sec = sum((v["duration"] or 0) for v in vs if v["emotion"] == "outrage")
    queries = [v["search_query"] for v in vs if v["search_query"]]
    start_dt = local(vs[0]["ts"], tz)
    domains = Counter()
    for v in vs:
        domains[v["domain"]] += v["duration"] or 0
    return {
        "minutes": total / 60,
        "visits": len(vs),
        "domains": len(domains),
        "top_domain": domains.most_common(1)[0][0] if domains else "",
        "top_category": cat_sec.most_common(1)[0][0] if cat_sec else "Other",
        "switches": switches,
        "switch_rate": switches / max(active_hr, 1 / 6),
        "avg_dwell": total / len(vs),
        "productive_share": share(productive),
        "leisure_share": share(K.LEISURE),
        "video_share": share(VIDEOISH),
        "learn_share": share(LEARNING),
        "utility_share": share(UTILITY),
        "feed_share": feed_sec / total,
        "doom_minutes": doom_min,
        "chain": chain,
        "late_share": late_sec / total,
        "neg_share": neg_sec / total,
        "outrage_share": outrage_sec / total,
        "searches": len(queries),
        "worry_searches": sum(1 for q in queries if is_worry_search(q)),
        "hour": start_dt.hour + start_dt.minute / 60,
        "evening": 1.0 if start_dt.hour >= 20 or start_dt.hour < 4 else 0.0,
        "valence_titles": (sum((v["valence"] or 0) * (v["duration"] or 0) for v in vs) / total),
    }


def score_states(f: dict, doom_threshold: float = 20) -> dict[str, float]:
    m = f["minutes"]
    sw = clamp(f["switch_rate"] / 60)
    dwell = clamp(f["avg_dwell"] / 180)
    s = {
        "Deep Focus": f["productive_share"] * (1 - sw) * (0.4 + 0.6 * dwell) * clamp(m / 25),
        "Learning": f["learn_share"] * clamp(m / 15) * (1 - 0.5 * sw),
        "Exploring": (clamp(f["domains"] / max(m / 60, 0.5) / 15) * (1 - f["leisure_share"])
                      * (1 - f["feed_share"]) * (1 - 0.7 * f["productive_share"]) * 0.7),
        "Rabbit Hole": clamp((f["chain"] - 3) / 8),
        "Errands": f["utility_share"] * 0.8,
        "Winding Down": f["evening"] * f["leisure_share"] * (1 - sw) * (1 - f["feed_share"]) * 0.7,
        "Escapism": f["video_share"] * clamp(m / 45),
        "Restless": sw * clamp(1 - f["avg_dwell"] / 120) * (1 - 0.5 * f["productive_share"]) * clamp(m / 10),
        "Doomscrolling": f["feed_share"] * clamp(f["doom_minutes"] / doom_threshold) * (1.0 if f["avg_dwell"] < 90 else 0.6),
        "Worry Spiral": clamp(f["worry_searches"] / 3) * 0.9 + f["neg_share"] * 0.3,
    }
    return {k: round(v, 3) for k, v in s.items()}


def pick_state(scores: dict[str, float]) -> str:
    state, value = max(scores.items(), key=lambda kv: kv[1])
    return state if value >= 0.15 else "Casual Browsing"


def mood(f: dict, state: str) -> tuple[float, float]:
    """(valence, arousal): state prior + headline tone + time-of-night."""
    prior = STATES[state]
    valence = 0.6 * prior["valence"] + 0.4 * f["valence_titles"] - 0.15 * f["late_share"]
    arousal = 0.5 * prior["arousal"] + 0.3 * clamp(f["switch_rate"] / 60) + 0.2 * f["feed_share"]
    return round(clamp(valence, -1, 1), 3), round(clamp(arousal), 3)


def mood_label(valence: float, arousal: float) -> str:
    if valence >= 0.05:
        return "engaged" if arousal >= 0.45 else "content"
    if valence <= -0.12:
        return "stressed" if arousal >= 0.5 else "drained"
    return "restless" if arousal >= 0.55 else "neutral"


# --- regret --------------------------------------------------------------------

def vector(f: dict) -> list[float]:
    h = 2 * math.pi * (f["hour"] / 24)
    return [math.log1p(f["minutes"]) / 5, f["feed_share"], f["leisure_share"], f["productive_share"],
            clamp(f["switch_rate"] / 60), f["late_share"], math.log1p(f["avg_dwell"]) / 7, f["neg_share"],
            clamp(f["doom_minutes"] / 30), clamp((f["chain"] - 3) / 8), math.sin(h), math.cos(h)]


def regret_prior(f: dict) -> float:
    z = (-1.8 + 2.0 * f["feed_share"] + 1.6 * f["late_share"] + 1.1 * f["leisure_share"] * clamp(f["minutes"] / 60)
         + 1.2 * clamp(f["doom_minutes"] / 30) + 0.8 * f["neg_share"] + 0.6 * clamp(f["switch_rate"] / 60)
         - 2.2 * f["productive_share"] - 0.8 * f["learn_share"])
    return 1 / (1 + math.exp(-z))


class RegretModel:
    """L2-regularized logistic regression in plain numpy (no sklearn needed)."""

    def __init__(self, weights=None, bias: float = 0.0):
        self.w = np.asarray(weights, dtype=float) if weights is not None else None
        self.b = bias

    def fit(self, X: np.ndarray, y: np.ndarray, l2: float = 0.5, epochs: int = 800, lr: float = 0.3):
        n, d = X.shape
        self.w, self.b = np.zeros(d), 0.0
        pos = y.mean()
        weight = np.where(y == 1, 0.5 / max(pos, 1e-6), 0.5 / max(1 - pos, 1e-6))  # balance classes
        for _ in range(epochs):
            p = self.predict(X)
            g = (p - y) * weight
            self.w -= lr * (X.T @ g / n + l2 * self.w / n)
            self.b -= lr * g.mean()
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return 1 / (1 + np.exp(-(X @ self.w + self.b)))

    def explain(self) -> list[tuple[str, float]]:
        return sorted(zip(FEATURES, self.w.round(3).tolist()), key=lambda kv: -abs(kv[1]))


def train_regret(db) -> dict | None:
    rows = db.q("SELECT label, features FROM session_labels")
    X, y = [], []
    for r in rows:
        try:
            X.append(vector(json.loads(r["features"])))
            y.append(1.0 if r["label"] == "regret" else 0.0)
        except (TypeError, ValueError, KeyError):
            continue
    if len(y) < 6 or len(set(y)) < 2:
        db.set_meta("regret_model", None)
        return None
    X_arr, y_arr = np.array(X), np.array(y)
    correct = 0
    if len(y) <= 300:                       # leave-one-out accuracy (honest small-data estimate)
        for i in range(len(y)):
            mask = np.arange(len(y)) != i
            if len(set(y_arr[mask])) < 2:
                continue
            m = RegretModel().fit(X_arr[mask], y_arr[mask], epochs=300)
            correct += int((m.predict(X_arr[i:i + 1])[0] >= 0.5) == (y_arr[i] == 1))
    model = RegretModel().fit(X_arr, y_arr)
    meta = {"weights": model.w.tolist(), "bias": model.b, "n": len(y), "positives": int(y_arr.sum()),
            "accuracy": round(correct / len(y), 3) if len(y) <= 300 else None,
            "drivers": model.explain()[:5], "trained": time.time()}
    db.set_meta("regret_model", meta)
    return meta


def load_regret(db) -> RegretModel | None:
    meta = db.get_meta("regret_model")
    return RegretModel(meta["weights"], meta["bias"]) if meta else None


def build(zen, visits: list[dict], progress=None) -> list[dict]:
    """Recompute all sessions from ``visits`` (sorted by device, ts) and persist them."""
    settings, db, tz = zen.settings, zen.db, zen.tz
    goals = settings["goals"]
    doom_threshold = settings["nudges"].get("doomscroll_minutes", 20)
    train_regret(db)
    model = load_regret(db)
    labels = {(r["device"], round(r["start"], 3)): r["label"] for r in db.q("SELECT device, start, label FROM session_labels")}
    out, visit_updates = [], []
    productive = set(goals.get("productive", []))
    chunks = [ep for sitting in split(visits) for ep in episodes(sitting, productive)]
    for sid, vs in enumerate(chunks, start=1):
        f = features(vs, goals, tz)
        scores = score_states(f, doom_threshold)
        state = pick_state(scores)
        valence, arousal = mood(f, state)
        prior = regret_prior(f)
        regret = prior if model is None else float(0.75 * model.predict(np.array([vector(f)]))[0] + 0.25 * prior)
        start, end = vs[0]["ts"], vs[-1]["ts"] + (vs[-1]["duration"] or 0)
        rec = {
            "id": sid, "device": vs[0]["device"], "start": start, "end": end,
            "day": vs[0]["day"], "visits": len(vs), "active_sec": f["minutes"] * 60, "domains": f["domains"],
            "switches": f["switches"], "top_category": f["top_category"], "top_domain": f["top_domain"],
            "state": state, "scores": scores, "features": f, "valence": valence, "arousal": arousal,
            "regret": round(regret, 3), "label": labels.get((vs[0]["device"], round(start, 3))),
        }
        out.append(rec)
        visit_updates.extend((sid, v["id"]) for v in vs)
        for v in vs:
            v["session_id"] = sid
    with db.write_lock:
        conn = db.connect()
        conn.execute("DELETE FROM sessions")
        conn.executemany(
            """INSERT INTO sessions(id, device, start, end, day, visits, active_sec, domains, switches,
               top_category, top_domain, state, scores, features, regret, label) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(s["id"], s["device"], s["start"], s["end"], s["day"], s["visits"], s["active_sec"], s["domains"],
              s["switches"], s["top_category"], s["top_domain"], s["state"], json.dumps(s["scores"]),
              json.dumps({**s["features"], "valence": s["valence"], "arousal": s["arousal"]}), s["regret"], s["label"])
             for s in out])
        conn.executemany("UPDATE visits SET session_id=? WHERE id=?", visit_updates)
        conn.commit()
    if progress:
        progress(f"> {len(out)} sessions; regret model: {'personal' if model else 'prior (label sessions to train it)'}")
    return out
