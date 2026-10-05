"""Zen-chan's voice: turn insights into plain, honest words.

Backends:
* ``local``     — deterministic templates, always available, fully offline.
* ``ollama``    — a local LLM through Ollama (still fully on-device).
* ``anthropic`` — Claude via the Anthropic API (opt-in; needs ANTHROPIC_API_KEY).

LLM backends receive an *aggregate digest* (categories, minutes, patterns)
— never URLs, and page titles only if ``narrator.share_titles`` is enabled.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

from .. import knowledge as K
from ..timeutil import fmt_minutes, now_local

log = logging.getLogger(__name__)

WINDOW_PHRASE = {"today": "Today so far", "yesterday": "Yesterday", "7d": "This week",
                 "30d": "The last 30 days", "all": "All time"}

PERSONA = (
    "You are Zen-chan, a warm, perceptive browsing-awareness buddy. You watch someone's browsing "
    "(with their consent, entirely for their benefit) and tell them what's really going on. Be "
    "specific and honest, never preachy or clinical; you are not a therapist and you never diagnose. "
    "Use the numbers you are given; don't invent any. Keep it short: a greeting line, 2-4 crisp "
    "observations, and one concrete, kind suggestion. If something looks like distress (worry "
    "searches, sleeplessness), gently suggest talking to someone they trust or a professional."
)


def greeting(hour: int) -> str:
    if hour < 5:
        return "Still up? Let's be gentle with tomorrow-you."
    if hour < 12:
        return "Good morning!"
    if hour < 17:
        return "Hey, good afternoon."
    if hour < 22:
        return "Good evening."
    return "Winding down?"


def local_summary(ins: dict, settings) -> dict:
    """Pick the most important truths from an insight document."""
    if ins.get("empty"):
        return {"face": K.FACES["sleepy"], "mood": "sleepy", "greeting": "Nothing to look at yet.",
                "headline": "No browsing in this window.", "observations": [], "suggestion": ""}
    p, ident, t = ins["patterns"], ins["identity"], ins["totals"]
    score = ins.get("score") or {}
    goals = settings["goals"]
    obs: list[tuple[int, str, str]] = []   # (priority, text, signal)

    if p["doom_minutes"] >= 15:
        sites = ", ".join(sorted({s for e in p["doomscroll"] for s in e["sites"]})[:3])
        late = any(e["late"] for e in p["doomscroll"])
        obs.append((90, f"{fmt_minutes(p['doom_minutes'])} went into {len(p['doomscroll'])} doomscroll "
                        f"run{'s' if len(p['doomscroll']) != 1 else ''} ({sites}), about "
                        f"{p['doomscroll'][0]['seconds_per_item']}s per item"
                        f"{' — some of it past bedtime' if late else ''}.", "doom"))
    if p["late"]["minutes"] >= 20:
        obs.append((85, f"You were online past your {goals.get('bedtime', '23:30')} bedtime on "
                        f"{p['late']['nights']} night{'s' if p['late']['nights'] != 1 else ''} "
                        f"({fmt_minutes(p['late']['minutes'])} total), latest at {p['late']['latest'] or '—'}.", "late"))
    if p["worry_searches"] >= 2:
        ex = p["worry_examples"][0] if p["worry_examples"] else ""
        obs.append((88, f"{p['worry_searches']} searches sounded worried (like “{ex}”). Late-night searching "
                        "tends to feed worry rather than settle it.", "worry"))
    today = now_local().strftime("%Y-%m-%d")
    for a in ins.get("anomalies", [])[:1]:
        who = "Today stands out" if a["day"] == today else f"{_day_name(a['day'])} stood out"
        obs.append((75, f"{who}: {a['reasons'][0].rstrip('.')}.", "anomaly"))
    if p["compulsive"]:
        c = p["compulsive"][0]
        obs.append((70, f"You opened {c['site']} about {c['per_day']:g}× a day for ~{c['avg_minutes']:g} min "
                        "each — that's a checking loop more than a choice.", "checks"))
    focus = next((s for s in ins["states"] if s["state"] == "Deep Focus"), None)
    if focus and focus["minutes"] >= 45:
        obs.append((65, f"{fmt_minutes(focus['minutes'])} of genuine deep focus across {focus['sessions']} "
                        f"session{'s' if focus['sessions'] != 1 else ''}. That's the good stuff.", "focus"))
    if p["outrage_share"] >= 0.3:
        obs.append((60, f"{p['outrage_share']:.0%} of the news and social posts you read were framed around "
                        "outrage or fear. That shapes mood more than it informs.", "outrage"))
    if p["switching"]["per_hour"] >= 45:
        obs.append((55, f"You switched sites ~{p['switching']['per_hour']:.0f}× per active hour — "
                        "attention is getting chopped into small pieces.", "switching"))
    if p["rabbit_holes"]:
        h = p["rabbit_holes"][0]
        obs.append((50, f"Rabbit hole spotted: {h['hops']} hops on {h['site']}, from “{_short(h['from'])}” "
                        f"to “{_short(h['to'])}”.", "rabbit"))
    drift = ident.get("drift") or {}
    if drift.get("risers"):
        r = drift["risers"][0]
        obs.append((45, f"Lately you're spending more time on {r['category']} (+{r['delta']:.0%} of your "
                        "browsing vs. the month before).", "drift"))
    gw = ins["habits"].get("gateways") or []
    if gw:
        g = gw[0]
        obs.append((42, f"{g['share']:.0%} of your {g['to']} time starts right after {g['from']}.", "gateway"))
    if not ident["headline"].startswith("Too early"):
        obs.append((40, f"Your browsing identity right now: {ident['headline']}.", "identity"))
    plats = ident["ad_profile"]["platforms"]
    if plats and plats[0]["share"] >= 0.35:
        obs.append((35, f"{plats[0]['company']} saw {plats[0]['share']:.0%} of your browsing first-hand.", "platform"))

    obs.sort(key=lambda o: -o[0])
    top = obs[:4]
    signals = {s for _, _, s in top}
    if "worry" in signals:
        suggestion = ("If the same worry keeps coming back, talk it through with someone you trust or a doctor — "
                      "a person helps more than another search. I'll keep an eye on late-night searching.")
        mood = "worried"
    elif "doom" in signals:
        suggestion = "Next time the scroll starts, try the 2-minute rule: notice it, close the tab, stand up. I'll nudge you at the 20-minute mark."
        mood = "worried"
    elif "late" in signals:
        suggestion = f"Let's try a browser bedtime at {goals.get('bedtime', '23:30')} — I'll ping you once when you pass it."
        mood = "sleepy"
    elif "checks" in signals:
        suggestion = f"Batch it: pick 3 fixed times a day for {p['compulsive'][0]['site']} and let the rest go."
        mood = "calm"
    elif "switching" in signals:
        suggestion = "One tab, one task for the next 25 minutes. Then a real break."
        mood = "alert"
    elif score.get("value", 0) >= 70:
        suggestion = "You're browsing on purpose. Keep it up — and remember breaks count as productive too."
        mood = "proud"
    else:
        suggestion = "Pick one thing you actually want from the internet today, and let me tell you when you drift."
        mood = "calm"
    hour = now_local().hour
    phrase = WINDOW_PHRASE.get(ins["window"], ins["window"])
    headline = (f"{phrase}: {fmt_minutes(t['minutes'])} online, intentionality {score['value']}/100 "
                f"({score['label'].lower()})." if score else f"{phrase}: {fmt_minutes(t['minutes'])} online.")
    return {"face": K.FACES.get(mood, K.FACES["calm"]), "mood": mood, "greeting": greeting(hour),
            "headline": headline, "observations": [text for _, text, _ in top], "suggestion": suggestion}


def _short(text: str, n: int = 40) -> str:
    text = re.sub(r"\s*[-|]\s*Wikipedia$", "", text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def _day_name(day: str) -> str:
    from datetime import date
    try:
        return date.fromisoformat(day).strftime("%A %d %b")
    except ValueError:
        return day


# --- LLM backends -------------------------------------------------------------

def digest(ins: dict, share_titles: bool = False) -> dict:
    """Compact aggregate view for an LLM. No URLs; titles only on opt-in."""
    if ins.get("empty"):
        return {"window": ins.get("window"), "empty": True}
    p = ins["patterns"]
    out = {
        "window": ins["window"], "totals": ins["totals"], "intentionality": ins.get("score"),
        "categories": [{k: c[k] for k in ("name", "minutes", "share")} for c in ins["categories"][:10]],
        "top_sites": [{k: d[k] for k in ("domain", "minutes", "category")} for d in ins["domains"][:8]],
        "states": [{k: s[k] for k in ("state", "minutes", "sessions")} for s in ins["states"]],
        "mood_inferred_from_browsing": {k: ins["mood"][k] for k in ("label", "valence", "arousal")},
        "doomscroll": {"minutes": p["doom_minutes"], "episodes": len(p["doomscroll"]),
                       "sites": sorted({s for e in p["doomscroll"] for s in e["sites"]})},
        "late_night": p["late"], "compulsive_checking": p["compulsive"][:3],
        "context_switches_per_hour": p["switching"]["per_hour"],
        "worry_searches": p["worry_searches"], "outrage_or_fear_share_of_news_social": p["outrage_share"],
        "rabbit_holes": [{"hops": h["hops"], "site": h["site"]} for h in p["rabbit_holes"][:3]],
        "identity": {"headline": ins["identity"]["headline"],
                     "archetypes": [{"name": a["name"], "score": a["score"]} for a in ins["identity"]["archetypes"][:4]],
                     "chronotype": ins["identity"]["chronotype"]["label"], "drift": ins["identity"]["drift"]},
        "anomalies": ins.get("anomalies", [])[:3],
        "devices": [{"label": d["label"], "kind": d["kind"], "minutes": d["minutes"]} for d in ins["devices"]],
    }
    if share_titles:
        out["worry_search_examples"] = p["worry_examples"]
        out["recent_searches"] = ins["searches"]["recent"][:10]
        out["rabbit_hole_paths"] = [{"from": h["from"], "to": h["to"]} for h in p["rabbit_holes"][:3]]
    return out


class LLMUnavailable(Exception):
    pass


def _ollama(settings, system: str, user: str) -> str:
    cfg = settings["narrator"]
    body = json.dumps({"model": cfg.get("ollama_model", "llama3.2"), "stream": False,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") + "/api/chat",
                                 data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LLMUnavailable(f"Ollama not reachable: {exc}") from exc
    return (data.get("message") or {}).get("content", "").strip()


def _anthropic(settings, system: str, user: str) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise LLMUnavailable("pip install anthropic to use the Claude narrator") from exc
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=settings["narrator"].get("anthropic_model", "claude-opus-5-5"),
            max_tokens=2048,
            system=system,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AuthenticationError as exc:
        raise LLMUnavailable("Anthropic API key missing or invalid (set ANTHROPIC_API_KEY)") from exc
    except anthropic.RateLimitError as exc:
        raise LLMUnavailable("Anthropic rate limit hit; try again shortly") from exc
    except anthropic.APIStatusError as exc:
        raise LLMUnavailable(f"Anthropic API error {exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMUnavailable("Could not reach the Anthropic API") from exc
    if response.stop_reason == "refusal":
        raise LLMUnavailable("Claude declined this request")
    return "".join(block.text for block in response.content if block.type == "text").strip()


def llm(settings, system: str, user: str) -> str:
    backend = settings["narrator"].get("backend", "local")
    if backend == "ollama":
        return _ollama(settings, system, user)
    if backend == "anthropic":
        return _anthropic(settings, system, user)
    raise LLMUnavailable("local backend")


def narrate(zen, window: str = "today") -> dict:
    ins = zen.db.get_insight(window) or {"window": window, "empty": True}
    base = ins.get("buddy") or local_summary(ins, zen.settings)
    backend = zen.settings["narrator"].get("backend", "local")
    if backend == "local" or ins.get("empty"):
        return {**base, "backend": "local"}
    payload = json.dumps(digest(ins, zen.settings["narrator"].get("share_titles", False)))
    try:
        text = llm(zen.settings, PERSONA, f"Here is my browsing digest ({window}). What's up with me?\n\n{payload}")
        return {**base, "text": text, "backend": backend}
    except LLMUnavailable as exc:
        return {**base, "backend": "local", "note": str(exc)}


# --- chat ----------------------------------------------------------------------

def chat(zen, message: str, window: str | None = None) -> dict:
    msg = (message or "").strip()
    low = msg.lower()
    window = window or ("today" if "today" in low else "yesterday" if "yesterday" in low
                        else "30d" if "month" in low else "all" if "all time" in low or "ever" in low else "7d")
    ins = zen.db.get_insight(window if window != "yesterday" else "7d") or {"window": window, "empty": True}
    backend = zen.settings["narrator"].get("backend", "local")
    note = ""
    if backend != "local" and not ins.get("empty"):
        payload = json.dumps(digest(ins, zen.settings["narrator"].get("share_titles", False)))
        try:
            text = llm(zen.settings, PERSONA + " Answer the user's question from the digest; if the digest "
                       "doesn't contain the answer, say so.", f"Digest ({window}):\n{payload}\n\nQuestion: {msg}")
            return {"reply": text, "backend": backend}
        except LLMUnavailable as exc:
            note = str(exc)
    reply = local_answer(zen, low, ins, window)
    return {"reply": reply, "backend": "local", **({"note": note} if note else {})}


def local_answer(zen, low: str, ins: dict, window: str) -> str:
    if ins.get("empty"):
        return "I don't have browsing data for that window yet. Hit refresh, or run `zenchan sync`."
    phrase = WINDOW_PHRASE.get(ins["window"], ins["window"]).lower()
    p, ident = ins["patterns"], ins["identity"]
    # time on a specific site or category
    for c in ins["categories"]:
        if c["name"].lower().split(" &")[0] in low:
            return f"{phrase.capitalize()}: {fmt_minutes(c['minutes'])} on {c['name']} ({c['share']:.0%} of your time)."
    site = re.search(r"([a-z0-9-]+\.(?:com|org|net|io|ai|in|co|tv|app|dev|gg)\b)|\b(youtube|reddit|twitter|instagram|netflix|github|tiktok|facebook|linkedin|amazon)\b", low)
    if site:
        name = site.group(1) or site.group(2)
        start = ins["start"]
        row = zen.db.one("SELECT SUM(duration) s, COUNT(*) n FROM visits WHERE ts >= ? AND (domain LIKE ? OR domain LIKE ?)",
                         (start, f"%{name}%", f"%{name}.%"))
        mins = (row["s"] or 0) / 60
        return (f"{phrase.capitalize()}: {fmt_minutes(mins)} on {name} across {row['n']} page views."
                if row["n"] else f"I don't see {name} in {phrase}.")
    if any(w in low for w in ("sleep", "late", "night", "bed")):
        l = p["late"]
        return (f"{phrase.capitalize()}: {fmt_minutes(l['minutes'])} past bedtime over {l['nights']} night(s), "
                f"latest at {l['latest'] or '—'}.") if l["minutes"] else f"No late-night browsing {phrase}."
    if any(w in low for w in ("focus", "productive", "work", "intentional", "score")):
        s = ins.get("score") or {}
        focus = next((x["minutes"] for x in ins["states"] if x["state"] == "Deep Focus"), 0)
        return (f"Intentionality {s.get('value', '—')}/100 ({s.get('label', '—').lower()}): "
                f"{fmt_minutes(ins['totals']['productive_minutes'])} on things you call productive, "
                f"{fmt_minutes(focus)} of deep focus.")
    if any(w in low for w in ("doom", "scroll", "feed")):
        return (f"{fmt_minutes(p['doom_minutes'])} of doomscrolling in {len(p['doomscroll'])} run(s) {phrase}."
                if p["doomscroll"] else f"No doomscroll runs {phrase}. (=^･ω･^=)")
    if any(w in low for w in ("who am i", "identity", "persona", "personality", "archetype")):
        arch = ", ".join(f"{a['name']} ({a['score']:.0%})" for a in ident["archetypes"][:3])
        return f"You read as a {ident['headline']}. Top archetypes: {arch}. Chronotype: {ident['chronotype']['label']}."
    if any(w in low for w in ("advert", "track", "know about me", "privacy", "data broker", "infer")):
        segs = ident["ad_profile"]["segments"][:4]
        plats = ident["ad_profile"]["platforms"][:3]
        segtext = "; ".join(f"{s['segment']} ({s['confidence']:.0%})" for s in segs) or "nothing strong"
        plattext = ", ".join(f"{x['company']} {x['share']:.0%}" for x in plats) or "no single platform"
        return f"An ad profile could plausibly infer: {segtext}. First-hand visibility: {plattext}."
    if any(w in low for w in ("mood", "feel", "vibe")):
        m = ins["mood"]
        return (f"Your browsing reads as {m['label']} {phrase} (valence {m['valence']:+.2f}, arousal {m['arousal']:.2f}). "
                "That's a reading of what you browsed, not of you — you know better than I do.")
    b = ins.get("buddy") or local_summary(ins, zen.settings)
    lines = " ".join(b["observations"][:3])
    if any(w in low for w in ("tip", "advice", "help", "should", "what do")):
        return b["suggestion"]
    return (f"{b['headline']} {lines} — Ask me about a site (\"how much youtube today?\"), sleep, focus, "
            "doomscrolling, your identity, or what advertisers could infer.")
