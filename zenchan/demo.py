"""Synthetic but realistic browsing data, written into *real browser schemas*.

``zenchan demo`` builds a fake Chrome profile (with a synced Android phone), a
Firefox work profile (with engagement metadata) and a Safari database (with
iPad visits arriving over iCloud), then runs the normal pipeline on them. The
same builders are used by the test-suite.
"""

from __future__ import annotations

import random
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from .browsers.chromium import unix_to_webkit
from .browsers.safari import MAC_EPOCH_OFFSET

PHONE_GUID = "a1b2c3d4-phone-0000-0000-000000000001"

# --------------------------------------------------------------------------
# Schema builders (subsets of the real schemas, column names match upstream)
# --------------------------------------------------------------------------


def build_chrome(path: Path, visits: list[dict], local_state_names: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE urls(id INTEGER PRIMARY KEY AUTOINCREMENT, url LONGVARCHAR, title LONGVARCHAR,
            visit_count INTEGER DEFAULT 0 NOT NULL, typed_count INTEGER DEFAULT 0 NOT NULL,
            last_visit_time INTEGER NOT NULL, hidden INTEGER DEFAULT 0 NOT NULL);
        CREATE TABLE visits(id INTEGER PRIMARY KEY AUTOINCREMENT, url INTEGER NOT NULL,
            visit_time INTEGER NOT NULL, from_visit INTEGER, transition INTEGER DEFAULT 0 NOT NULL,
            segment_id INTEGER, visit_duration INTEGER DEFAULT 0 NOT NULL,
            incremented_omnibox_typed_score BOOLEAN DEFAULT FALSE NOT NULL,
            opener_visit INTEGER, originator_cache_guid TEXT, originator_visit_id INTEGER,
            originator_from_visit INTEGER, originator_opener_visit INTEGER,
            is_known_to_sync BOOLEAN DEFAULT FALSE NOT NULL,
            consider_for_ntp_most_visited BOOLEAN DEFAULT FALSE NOT NULL,
            external_referrer_url TEXT, visited_link_id INTEGER DEFAULT 0 NOT NULL, app_id TEXT);
        CREATE TABLE keyword_search_terms(keyword_id INTEGER NOT NULL, url_id INTEGER NOT NULL,
            term LONGVARCHAR NOT NULL, normalized_term LONGVARCHAR NOT NULL);
    """)
    url_ids: dict[str, int] = {}
    for v in visits:
        url = v["url"]
        if url not in url_ids:
            cur = conn.execute("INSERT INTO urls(url, title, last_visit_time) VALUES (?,?,?)",
                               (url, v["title"], unix_to_webkit(v["ts"])))
            url_ids[url] = cur.lastrowid
            if v.get("search"):
                conn.execute("INSERT INTO keyword_search_terms VALUES (2, ?, ?, ?)",
                             (url_ids[url], v["search"], v["search"].lower()))
        core = {"link": 0, "typed": 1, "bookmark": 2, "reload": 8, "keyword": 9}.get(v.get("transition", "link"), 0)
        transition = core | 0x10000000 | 0x20000000  # CHAIN_START | CHAIN_END
        conn.execute("""INSERT INTO visits(url, visit_time, transition, visit_duration, originator_cache_guid)
                        VALUES (?,?,?,?,?)""",
                     (url_ids[url], unix_to_webkit(v["ts"]), transition,
                      int(v.get("tab_seconds", v["dur"]) * 1_000_000), v.get("guid", "")))
    # a redirect hop and an iframe that must be ignored by the reader
    if visits:
        t = unix_to_webkit(visits[0]["ts"] - 5)
        cur = conn.execute("INSERT INTO urls(url, title, last_visit_time) VALUES ('https://t.co/xyz', '', ?)", (t,))
        conn.execute("INSERT INTO visits(url, visit_time, transition) VALUES (?,?,?)",
                     (cur.lastrowid, t, 0 | 0x10000000 | 0x80000000))
        conn.execute("INSERT INTO visits(url, visit_time, transition) VALUES (?,?,?)", (cur.lastrowid, t, 3))
    conn.commit()
    conn.close()


def write_local_state(user_data: Path, names: dict[str, str]) -> None:
    import json
    info = {pid: {"name": name} for pid, name in names.items()}
    (user_data / "Local State").write_text(json.dumps({"profile": {"info_cache": info}}), encoding="utf-8")


def build_firefox(path: Path, visits: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE moz_places(id INTEGER PRIMARY KEY, url LONGVARCHAR, title LONGVARCHAR,
            rev_host LONGVARCHAR, visit_count INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0 NOT NULL,
            typed INTEGER DEFAULT 0 NOT NULL, frecency INTEGER DEFAULT -1 NOT NULL,
            last_visit_date INTEGER, guid TEXT, foreign_count INTEGER DEFAULT 0 NOT NULL,
            url_hash INTEGER DEFAULT 0 NOT NULL, description TEXT, preview_image_url TEXT,
            site_name TEXT, origin_id INTEGER);
        CREATE TABLE moz_historyvisits(id INTEGER PRIMARY KEY, from_visit INTEGER, place_id INTEGER,
            visit_date INTEGER, visit_type INTEGER, session INTEGER, source INTEGER DEFAULT 0 NOT NULL,
            triggeringPlaceId INTEGER);
        CREATE TABLE moz_places_metadata(id INTEGER PRIMARY KEY, place_id INTEGER NOT NULL,
            referrer_place_id INTEGER, created_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0, total_view_time INTEGER NOT NULL DEFAULT 0,
            typing_time INTEGER NOT NULL DEFAULT 0, key_presses INTEGER NOT NULL DEFAULT 0,
            scrolling_time INTEGER NOT NULL DEFAULT 0, scrolling_distance INTEGER NOT NULL DEFAULT 0,
            document_type INTEGER NOT NULL DEFAULT 0, search_query_id INTEGER);
    """)
    place_ids: dict[str, int] = {}
    for v in visits:
        if v["url"] not in place_ids:
            cur = conn.execute("INSERT INTO moz_places(url, title, last_visit_date) VALUES (?,?,?)",
                               (v["url"], v["title"], int(v["ts"] * 1e6)))
            place_ids[v["url"]] = cur.lastrowid
        pid = place_ids[v["url"]]
        conn.execute("INSERT INTO moz_historyvisits(place_id, visit_date, visit_type, source) VALUES (?,?,?,?)",
                     (pid, int(v["ts"] * 1e6), 2 if v.get("transition") == "typed" else 1,
                      4 if v.get("synced") else 0))
        if v.get("measured"):
            conn.execute("""INSERT INTO moz_places_metadata(place_id, created_at, updated_at,
                            total_view_time, scrolling_distance) VALUES (?,?,?,?,?)""",
                         (pid, int(v["ts"] * 1000) + 300, int((v["ts"] + v["dur"]) * 1000),
                          int(v["dur"] * 1000), int(v.get("scroll", 0))))
    conn.commit()
    conn.close()


def write_profiles_ini(base: Path, profiles: dict[str, str]) -> None:
    lines = []
    for i, (folder, name) in enumerate(profiles.items()):
        lines += [f"[Profile{i}]", f"Name={name}", "IsRelative=1", f"Path=Profiles/{folder}", ""]
    base.mkdir(parents=True, exist_ok=True)
    (base / "profiles.ini").write_text("\n".join(lines), encoding="utf-8")


def build_safari(path: Path, visits: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE history_items(id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL UNIQUE,
            domain_expansion TEXT NULL, visit_count INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE history_visits(id INTEGER PRIMARY KEY AUTOINCREMENT, history_item INTEGER NOT NULL,
            visit_time REAL NOT NULL, title TEXT NULL, load_successful BOOLEAN NOT NULL DEFAULT 1,
            http_non_get BOOLEAN NOT NULL DEFAULT 0, synthesized BOOLEAN NOT NULL DEFAULT 0,
            redirect_source INTEGER NULL, redirect_destination INTEGER NULL,
            origin INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 0,
            attributes INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 0);
    """)
    item_ids: dict[str, int] = {}
    for v in visits:
        if v["url"] not in item_ids:
            cur = conn.execute("INSERT INTO history_items(url, visit_count) VALUES (?, 1)", (v["url"],))
            item_ids[v["url"]] = cur.lastrowid
        conn.execute("INSERT INTO history_visits(history_item, visit_time, title, origin) VALUES (?,?,?,?)",
                     (item_ids[v["url"]], v["ts"] - MAC_EPOCH_OFFSET, v["title"], 1 if v.get("synced") else 0))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# A persona: a developer who studies, job-hunts, and doomscrolls at night
# --------------------------------------------------------------------------

def _slug(text: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in text.lower()).split())[:60]


SO_QUESTIONS = ["How to merge two dictionaries in Python", "Why does my async function return a coroutine",
                "SQLite database is locked error when using threads", "Flask CORS preflight request fails",
                "numpy broadcasting operands could not be broadcast together", "git rebase vs merge which one should I use",
                "React useEffect runs twice in development", "Docker container exits immediately after start",
                "Python virtualenv not activating on Windows PowerShell", "Regex to match an email address",
                "How do I read a file line by line in Rust", "Pandas groupby with multiple aggregations"]
REPOS = [("pallets/flask", "The Python micro framework for building web applications"),
         ("huggingface/transformers", "State-of-the-art Machine Learning for PyTorch"),
         ("fastapi/fastapi", "FastAPI framework, high performance, easy to learn"),
         ("rust-lang/rust", "Empowering everyone to build reliable and efficient software"),
         ("sivOclock/Zen_Chan", "Local-first browsing awareness buddy"),
         ("scikit-learn/scikit-learn", "scikit-learn: machine learning in Python")]
PY_DOCS = ["asyncio", "sqlite3", "pathlib", "itertools", "dataclasses", "zoneinfo"]
MDN = ["Fetch_API", "EventSource", "Service_Worker_API", "Canvas_API", "Notifications_API"]
TUTORIALS = ["Python Full Course for Beginners", "But what is a neural network? | Deep learning chapter 1",
             "Rust in 100 Seconds", "Learn Docker in 1 Hour - Full Tutorial", "System Design Interview: Design a URL Shortener",
             "Transformers explained: attention is all you need"]
FUN_VIDEOS = ["Top 10 anime fights of all time", "I survived 100 days in hardcore Minecraft",
              "Funniest cat videos compilation 2026", "Speedrunner explains the impossible glitch",
              "Reacting to the worst cooking fails", "Lofi hip hop radio - beats to relax/study to",
              "Every Marvel movie ranked", "What happens if you never sleep?"]
SUBREDDITS = {"programming": "Why I stopped using ORMs", "AskReddit": "What's a skill everyone should learn?",
              "memes": "me_irl", "worldnews": "Leaders slam rival nation in furious summit exchange",
              "cscareerquestions": "Is the job market getting better?", "pcgaming": "Steam autumn sale megathread",
              "personalfinance": "How do I start investing with little money?", "mildlyinfuriating": "This parking job",
              "anxiety": "Can't sleep again, mind won't stop racing"}
NEWS = [("https://www.bbc.com/news/world-{n}", "Global markets fall as recession fears grow - BBC News"),
        ("https://www.theguardian.com/world/{n}", "Backlash grows as minister exposed in funding scandal | The Guardian"),
        ("https://www.reuters.com/technology/{n}", "Chipmaker unveils new AI accelerator | Reuters"),
        ("https://www.ndtv.com/india-news/{n}", "Heavy rain alert issued for several states - NDTV"),
        ("https://apnews.com/article/{n}", "Senator slams tech CEOs in heated hearing | AP News")]
WIKI_CHAIN = ["Byzantine_Empire", "Fall_of_Constantinople", "Ottoman_Empire", "Suleiman_the_Magnificent",
              "Siege_of_Vienna", "Habsburg_monarchy", "Holy_Roman_Empire", "Voltaire", "Candide",
              "Optimism_(philosophy)", "Gottfried_Wilhelm_Leibniz", "Calculus", "Isaac_Newton"]
WORRY = ["can't sleep at night anxiety", "chest pain when anxious is it normal", "why do i feel tired all the time",
         "how to stop overthinking at night", "heart racing at night"]
SHOPPING = [("https://www.rtings.com/laptop/reviews/best/programming", "The 6 Best Laptops For Programming - Fall 2026: Reviews - RTINGS.com"),
            ("https://www.amazon.in/dp/B0F{n}", "Apple 2026 MacBook Air Laptop with M4 chip: Amazon.in"),
            ("https://www.flipkart.com/lenovo-thinkpad/p/itm{n}", "Lenovo ThinkPad X1 Carbon Gen 13 price in India - Flipkart"),
            ("https://www.gsmarena.com/compare.php3?idPhone1={n}", "Compare Pixel 10 vs Galaxy S26 - GSMArena.com")]
JOBS = [("https://in.indeed.com/jobs?q=backend+engineer&l=Bengaluru", "Backend Engineer Jobs in Bengaluru, Karnataka - Indeed"),
        ("https://www.linkedin.com/jobs/view/{n}", "Senior Python Engineer - Remote | LinkedIn"),
        ("https://www.glassdoor.co.in/Salaries/software-engineer-salary-SRCH_KO0,17.htm", "Software Engineer Salaries | Glassdoor"),
        ("https://www.levels.fyi/t/software-engineer", "Software Engineer Salary | Levels.fyi")]
TRAVEL = [("https://www.skyscanner.co.in/transport/flights/blr/tyoa/", "Cheap flights from Bengaluru to Tokyo | Skyscanner"),
          ("https://www.booking.com/searchresults.html?ss=Shibuya", "Hotels in Shibuya, Tokyo - Booking.com"),
          ("https://www.google.com/maps/place/Shibuya+Crossing", "Shibuya Crossing - Google Maps")]


class Persona:
    def __init__(self, seed: int = 7):
        self.r = random.Random(seed)
        self.n = 1000

    def _id(self) -> int:
        self.n += self.r.randint(1, 97)
        return self.n

    def item(self, kind: str) -> tuple[str, str, float, dict]:
        """(url, title, dwell_seconds, extras) for one page of the given kind."""
        r = self.r
        if kind == "code":
            choice = r.random()
            if choice < 0.35:
                q = r.choice(SO_QUESTIONS)
                return (f"https://stackoverflow.com/questions/{self._id()}/{_slug(q)}", f"{q} - Stack Overflow", r.uniform(60, 420), {})
            if choice < 0.6:
                repo, desc = r.choice(REPOS)
                return (f"https://github.com/{repo}", f"GitHub - {repo}: {desc}", r.uniform(60, 600), {})
            if choice < 0.8:
                mod = r.choice(PY_DOCS)
                return (f"https://docs.python.org/3/library/{mod}.html", f"{mod} — Python 3.13 documentation", r.uniform(90, 600), {})
            api = r.choice(MDN)
            return (f"https://developer.mozilla.org/en-US/docs/Web/API/{api}", f"{api.replace('_', ' ')} - Web APIs | MDN", r.uniform(60, 400), {})
        if kind == "ai":
            topic = r.choice(["Debugging SQLite locking in Flask", "Explain attention in transformers",
                              "Write a regex for log parsing", "Refactor this React component"])
            host = r.choice(["https://claude.ai/chat/", "https://chatgpt.com/c/"])
            return (host + f"{self._id():x}", topic, r.uniform(120, 900), {})
        if kind == "search_code":
            q = r.choice(["python sqlite database is locked", "flask sse stream example", "numpy kmeans from scratch",
                          "service worker cache strategies", "rust borrow checker explained"])
            return (f"https://www.google.com/search?q={quote_plus(q)}", f"{q} - Google Search", r.uniform(8, 40), {"search": q})
        if kind == "tutorial":
            t = r.choice(TUTORIALS)
            return (f"https://www.youtube.com/watch?v=T{self._id()}", f"{t} - YouTube", r.uniform(600, 1800), {})
        if kind == "course":
            return ("https://www.coursera.org/learn/neural-networks-deep-learning", "Neural Networks and Deep Learning | Coursera", r.uniform(900, 2400), {})
        if kind == "work":
            t = r.choice([("https://acme.atlassian.net/browse/ZEN-{n}", "ZEN-{n} Fix login redirect loop - Jira"),
                          ("https://docs.google.com/document/d/{n}/edit", "Q4 roadmap planning - Google Docs"),
                          ("https://www.notion.so/acme/Sprint-{n}", "Sprint {n} retro notes - Notion"),
                          ("https://app.slack.com/client/T0{n}/C0{n}", "general (Channel) - Acme - Slack")])
            n = self._id()
            return (t[0].format(n=n), t[1].format(n=n % 400), r.uniform(120, 900), {})
        if kind == "mail":
            return ("https://mail.google.com/mail/u/0/#inbox", f"Inbox ({r.randint(1, 40)}) - alex@example.com - Gmail", r.uniform(20, 120), {})
        if kind == "news":
            url, title = r.choice(NEWS)
            return (url.format(n=self._id()), title, r.uniform(40, 200), {})
        if kind == "reddit":
            sub, title = r.choice(list(SUBREDDITS.items()))
            return (f"https://www.reddit.com/r/{sub}/comments/{self._id():x}/{_slug(title)}/", f"{title} : r/{sub}", r.uniform(10, 70), {})
        if kind == "x":
            return ("https://x.com/home", "Home / X", r.uniform(15, 90), {})
        if kind == "insta":
            return (f"https://www.instagram.com/reels/C{self._id()}/", "Instagram", r.uniform(8, 40), {})
        if kind == "shorts":
            return (f"https://www.youtube.com/shorts/S{self._id()}", f"{r.choice(FUN_VIDEOS)} #shorts - YouTube", r.uniform(10, 50), {})
        if kind == "fun_video":
            return (f"https://www.youtube.com/watch?v=F{self._id()}", f"{r.choice(FUN_VIDEOS)} - YouTube", r.uniform(300, 1300), {})
        if kind == "netflix":
            return ("https://www.netflix.com/watch/8100" + str(r.randint(10, 99)), "Netflix", r.uniform(1500, 3000), {})
        if kind == "gaming":
            t = r.choice([("https://store.steampowered.com/app/1245620/ELDEN_RING/", "ELDEN RING on Steam"),
                          ("https://game8.co/games/Elden-Ring/archives/{n}", "Best Builds for Elden Ring | Game8"),
                          ("https://www.chess.com/play/online", "Play Chess Online Against People - Chess.com")])
            return (t[0].format(n=self._id()), t[1], r.uniform(200, 1200), {})
        if kind == "sports":
            return (f"https://www.cricbuzz.com/live-cricket-scores/{self._id()}/ind-vs-aus", "IND vs AUS, 3rd ODI live score - Cricbuzz", r.uniform(60, 400), {})
        if kind == "music":
            return ("https://open.spotify.com/playlist/37i9dQZF1DX8Uebhn9wzrS", "lofi beats - playlist | Spotify", r.uniform(60, 300), {})
        if kind == "shopping":
            url, title = r.choice(SHOPPING)
            return (url.format(n=self._id()), title, r.uniform(60, 400), {})
        if kind == "search_shop":
            q = r.choice(["best laptop for programming 2026", "macbook air m4 vs thinkpad x1", "pixel 10 vs galaxy s26 camera"])
            return (f"https://www.google.com/search?q={quote_plus(q)}", f"{q} - Google Search", r.uniform(8, 40), {"search": q})
        if kind == "jobs":
            url, title = r.choice(JOBS)
            return (url.format(n=self._id()), title, r.uniform(60, 400), {})
        if kind == "travel":
            url, title = r.choice(TRAVEL)
            return (url, title, r.uniform(60, 500), {})
        if kind == "food":
            return ("https://www.allrecipes.com/recipe/2" + str(r.randint(10000, 99999)) + "/easy-chicken-biryani/",
                    "Easy Chicken Biryani Recipe - Allrecipes", r.uniform(120, 500), {})
        if kind == "worry":
            q = r.choice(WORRY)
            return (f"https://www.google.com/search?q={quote_plus(q)}", f"{q} - Google Search", r.uniform(20, 90), {"search": q})
        if kind == "webmd":
            return ("https://www.webmd.com/sleep-disorders/insomnia-symptoms-and-causes", "Insomnia: Symptoms, Causes, and Treatments - WebMD", r.uniform(90, 300), {})
        if kind == "finance":
            return ("https://www.tradingview.com/chart/?symbol=NSE%3ANIFTY", "NIFTY 50 — Chart — TradingView", r.uniform(60, 300), {})
        raise KeyError(kind)


def _block(p: Persona, out: list, start: float, minutes: float, weights: dict[str, float], **extra) -> float:
    """Generate a run of visits starting at ``start``; returns the end time."""
    t, end = start, start + minutes * 60
    kinds, w = zip(*weights.items())
    while t < end:
        kind = p.r.choices(kinds, w)[0]
        url, title, dwell, more = p.item(kind)
        visit = {"ts": t, "url": url, "title": title, "dur": dwell,
                 "tab_seconds": dwell * p.r.uniform(1.0, 6.0), "transition": "link", **more, **extra}
        out.append(visit)
        t += dwell + p.r.uniform(2, 25)
    return t


def generate(days: int = 21, seed: int = 7, now: datetime | None = None) -> dict[str, list[dict]]:
    """Returns visits per browser: {'chrome': [...], 'firefox': [...], 'safari': [...]}."""
    now = now or datetime.now().astimezone()
    p = Persona(seed)
    chrome: list[dict] = []
    firefox: list[dict] = []
    safari: list[dict] = []
    phone = {"guid": PHONE_GUID}
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    anomaly_day = days - 4

    for d in range(days - 1, -1, -1):
        day0 = today0 - timedelta(days=d)
        idx = days - 1 - d                     # 0 = oldest
        weekend = day0.weekday() >= 5
        recent = idx >= days - 7               # last week: job hunt + laptop shopping
        at = lambda h, m=0: (day0 + timedelta(hours=h, minutes=m)).timestamp()
        jitter = lambda: p.r.uniform(-12, 12) * 60

        # Morning check-in ritual
        _block(p, chrome, at(8, 10) + jitter(), 18, {"mail": 3, "news": 3, "reddit": 2, "x": 1})
        if not weekend:
            # Work (Firefox work profile, real engagement metadata)
            t = _block(p, firefox, at(9, 40) + jitter(), 95, {"code": 6, "ai": 2, "work": 3, "search_code": 2, "reddit": 0.6, "x": 0.4},
                       measured=True, scroll=0)
            _block(p, chrome, t + 60, 12, {"reddit": 3, "x": 2, "news": 1})
            _block(p, firefox, t + 15 * 60, 70, {"code": 5, "work": 3, "ai": 2, "tutorial": 1}, measured=True)
            _block(p, chrome, at(13, 10), 45, {"fun_video": 3, "reddit": 3, "shorts": 2, "food": 0.5})
            _block(p, firefox, at(14, 15), 150, {"code": 5, "work": 4, "ai": 2, "course": 1, "x": 0.6, "reddit": 0.6}, measured=True)
            if recent:
                _block(p, chrome, at(17, 30), 40, {"jobs": 4, "search_shop": 1, "shopping": 3})
        else:
            _block(p, chrome, at(10, 30), 120, {"fun_video": 3, "gaming": 4, "reddit": 2, "sports": 1, "music": 1})
            _block(p, chrome, at(13, 0), 60, {"tutorial": 2, "course": 2, "code": 2})
            if idx % 7 == 5:
                _block(p, chrome, at(15, 0), 50, {"travel": 5, "food": 1})
        # Wikipedia rabbit hole (twice a week)
        if idx % 3 == 1:
            t = at(16, 5) if weekend else at(12, 20)
            for title in WIKI_CHAIN[: p.r.randint(8, len(WIKI_CHAIN))]:
                chrome.append({"ts": t, "url": f"https://en.wikipedia.org/wiki/{title}",
                               "title": f"{title.replace('_', ' ')} - Wikipedia", "dur": p.r.uniform(70, 240),
                               "transition": "link"})
                t += chrome[-1]["dur"] + 5
        # Evening
        _block(p, chrome, at(19, 30) + jitter(), 30, {"food": 1, "sports": 2, "reddit": 2, "music": 1})
        _block(p, chrome, at(20, 30), 100 if weekend else 70, {"netflix": 2, "fun_video": 3, "gaming": 1})
        # iPad reading in bed via Safari/iCloud
        if idx % 2 == 0:
            _block(p, safari, at(22, 15), 25, {"news": 3, "reddit": 2, "tutorial": 1}, synced=True)
        # Late night on the phone (synced into desktop Chrome)
        late = weekend or idx % 3 == 0 or idx == anomaly_day
        if late:
            minutes = 170 if idx == anomaly_day else p.r.uniform(35, 80)
            t = _block(p, chrome, at(23, 40), minutes, {"insta": 4, "reddit": 3, "x": 2, "shorts": 4}, **phone)
            if idx % 2 == 0 or idx == anomaly_day:
                _block(p, chrome, t + 30, 10, {"worry": 3, "webmd": 1, "reddit": 0.5}, **phone)

    cutoff = now.timestamp()
    # Right now: a doomscroll in progress (so the watcher has something to say)
    t = cutoff - 28 * 60
    while t < cutoff - 30:
        url, title, dwell, more = p.item(p.r.choice(["reddit", "reddit", "x", "shorts"]))
        chrome.append({"ts": t, "url": url, "title": title, "dur": dwell, "transition": "link", **more})
        t += min(dwell, 40) + 3

    keep = lambda rows: sorted((v for v in rows if v["ts"] < cutoff), key=lambda v: v["ts"])
    return {"chrome": keep(chrome), "firefox": keep(firefox), "safari": keep(safari)}


def install(home: Path, days: int = 21, seed: int = 7) -> dict:
    """Write the demo browser databases under ``home/demo-browsers``; returns extra_sources."""
    data = generate(days=days, seed=seed)
    root = home / "demo-browsers"
    chrome_ud = root / "chrome" / "User Data"
    build_chrome(chrome_ud / "Default" / "History", data["chrome"])
    write_local_state(chrome_ud, {"Default": "Alex (personal)"})
    ff_base = root / "firefox"
    write_profiles_ini(ff_base, {"w0rk.work": "Work"})
    build_firefox(ff_base / "Profiles" / "w0rk.work" / "places.sqlite", data["firefox"])
    build_safari(root / "safari" / "History.db", data["safari"])
    return {
        "discover_system": False,
        "extra_sources": [
            {"engine": "chromium", "path": str(chrome_ud), "browser": "Chrome"},
            {"engine": "firefox", "path": str(ff_base), "browser": "Firefox"},
            {"engine": "safari", "path": str(root / "safari"), "browser": "Safari"},
        ],
        "device_aliases": {"chrome-sync:a1b2c3d4": "Pixel (Android)", "icloud": "iPad"},
        "counts": {k: len(v) for k, v in data.items()},
        "generated": time.time(),
    }
