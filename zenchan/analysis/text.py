"""URL/title parsing and lightweight text features."""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import parse_qs, unquote_plus, urlsplit

from .. import knowledge as K

TWO_PART_SUFFIXES = {"co.uk", "co.in", "co.jp", "com.au", "co.nz", "com.br", "co.za", "com.mx",
                     "org.uk", "ac.in", "gov.in", "ac.uk", "com.sg", "com.tr", "co.kr", "com.cn"}

STOPWORDS = set("""a an the and or but if of in on at to for from by with without about into over
after before between under is are was were be been being it its this that these those i you he she
we they me my your our their his her them us what which who whom how why when where do does did
done not no yes so than too very can will just should would could may might must also there here
vs via new more most best top all any some one two three get got make made use using used out up
down off again further then once only own same other such each both few nor s t don now video
official full hd 4k part ep amp quot www com http https html php youtube google search reddit""".split())

TOKEN = re.compile(r"[a-z][a-z0-9+#.'-]{1,}")


@lru_cache(maxsize=200_000)
def domain_of(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    host = host.lower().rstrip(".")
    for prefix in ("www.", "m.", "mobile.", "amp."):
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix):]
            break
    return host


def is_mobile_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host.startswith(("m.", "mobile.")) or "/amp/" in url or host.startswith("amp.")


def registrable(domain: str) -> str:
    parts = domain.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def brand(domain: str) -> str:
    """'news.google.co.in' -> 'google'."""
    reg = registrable(domain)
    return reg.split(".")[0] if reg else ""


def domain_chain(domain: str) -> list[str]:
    """'a.b.example.com' -> ['a.b.example.com', 'b.example.com', 'example.com']."""
    parts = domain.split(".")
    reg = registrable(domain)
    out = []
    for i in range(len(parts)):
        candidate = ".".join(parts[i:])
        out.append(candidate)
        if candidate == reg:
            break
    return out


def search_query(url: str, domain: str | None = None) -> str | None:
    domain = domain or domain_of(url)
    for pattern, param in K.SEARCH_PARAMS:
        if pattern.search(domain):
            try:
                qs = parse_qs(urlsplit(url).query)
            except ValueError:
                return None
            values = qs.get(param)
            if values and values[0].strip():
                return unquote_plus(values[0]).strip()[:300]
            return None
    return None


def subreddit(url: str) -> str | None:
    m = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)", url)
    return m.group(1).lower() if m else None


def clean_title(title: str, domain: str = "") -> str:
    t = title or ""
    t = re.sub(r"^\(\d+\+?\)\s*", "", t)                      # "(3) Inbox" notification counters
    t = re.sub(r"\s*[-|–—•]\s*(YouTube|Reddit|Wikipedia|Google Search|Medium|X|Twitter)$", "", t, flags=re.I)
    return t.strip()


def tokens(text: str) -> list[str]:
    return [w.strip(".'-") for w in TOKEN.findall((text or "").lower()) if w.strip(".'-") not in STOPWORDS and len(w.strip(".'-")) > 2]


def emotion(text: str) -> tuple[str | None, float]:
    """Dominant emotional framing of a title and a valence in [-1, 1]."""
    if not text:
        return None, 0.0
    hits = {name: len(rx.findall(text)) for name, rx in K.EMOTIONS.items()}
    pos = sum(hits[e] for e in K.POSITIVE)
    neg = sum(hits[e] for e in K.NEGATIVE) * 1.2   # negativity bias: headlines lean on it
    total = pos + neg
    if not total:
        return None, 0.0
    top = max(hits, key=lambda k: (hits[k], k in K.NEGATIVE))
    return top, round((pos - neg) / (total + 1.0), 3)


def is_worry_search(query: str | None) -> bool:
    return bool(query) and bool(K.WORRY_SEARCH.search(query))
