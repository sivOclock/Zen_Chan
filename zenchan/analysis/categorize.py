"""Assign every visit a category.

Layered, most-reliable first:
1. URL rules (``linkedin.com/jobs`` is job hunting, not social media)
2. Known domains (~800 popular sites)
3. For "soft" domains (YouTube, Reddit, Medium, ...) the *title* decides,
   because a Rust tutorial on YouTube is learning, not entertainment
4. Keyword rules over the title
5. Zero-shot semantic match against category descriptions (MiniLM only)
"""

from __future__ import annotations

from urllib.parse import urlsplit

import numpy as np

from .. import knowledge as K
from .text import domain_chain, subreddit

# categories a soft domain's title is allowed to pull it into
TITLE_OVERRIDES = {"Programming & Dev", "AI Tools", "Learning & Education", "Science", "News & Politics",
                   "Music & Audio", "Gaming", "Sports", "Tech News & Gadgets", "Finance & Crypto",
                   "Health & Fitness", "Food & Cooking", "Jobs & Career", "Entertainment & Pop Culture",
                   "Art & Design", "Research & Reference"}


def keyword_category(text: str) -> tuple[str | None, int]:
    if not text:
        return None, 0
    best, best_score = None, 0
    for cat, rx in K.KEYWORD_RE.items():
        score = len(rx.findall(text))
        if score > best_score:
            best, best_score = cat, score
    return best, best_score


def subreddit_category(sub: str) -> str | None:
    for rx, cat in K.SUBREDDIT_HINTS:
        if rx.search(sub):
            return cat
    return None


class Categorizer:
    def __init__(self, embedder=None):
        self.embedder = embedder if (embedder is not None and getattr(embedder, "semantic", False)) else None
        self.path_rules = sorted(((k, v) for k, v in K.DOMAINS.items() if "/" in k), key=lambda kv: -len(kv[0]))
        self._protos = None

    def rule_based(self, url: str, domain: str, title: str) -> tuple[str, float, str] | None:
        low = url.lower()
        for rx, cat in K.URL_RULES:
            if rx.search(low):
                return cat, 0.95, "url"
        try:
            hostpath = domain + urlsplit(url).path.lower()
        except ValueError:
            hostpath = domain
        for prefix, cat in self.path_rules:
            if hostpath.startswith(prefix):
                return cat, 0.95, "url"
        for d in domain_chain(domain):
            cat = K.DOMAINS.get(d)
            if not cat:
                continue
            if d in K.SUBREDDIT_DOMAINS:
                hint = subreddit_category(subreddit(url) or "")
                return (hint, 0.85, "subreddit") if hint else (cat, 0.9, "domain")
            if d in K.SOFT_DOMAINS:
                kw, score = keyword_category(title)
                if kw in TITLE_OVERRIDES and kw != cat and score >= 1:
                    return kw, 0.7, "title"
                return cat, 0.75, "domain"
            return cat, 0.95, "domain"
        kw, score = keyword_category(f"{title} {domain.replace('.', ' ')}")
        if kw and score >= 1:
            return kw, min(0.4 + 0.15 * score, 0.8), "keywords"
        return None

    def _prototypes(self):
        if self._protos is None:
            names = [c for c in K.CATEGORIES if c not in ("Other", "Search", "Adult")]
            descs = [f"{c}: {K.CATEGORIES[c]['desc']}" for c in names]
            self._protos = (names, self.embedder.encode(descs))
        return self._protos

    def semantic(self, titles: list[str]) -> list[tuple[str, float]]:
        """Zero-shot: nearest category description in embedding space."""
        if not self.embedder or not titles:
            return [("Other", 0.0)] * len(titles)
        names, protos = self._prototypes()
        vecs = self.embedder.encode(titles)
        sims = vecs @ protos.T
        idx = sims.argmax(axis=1)
        out = []
        for row, j in enumerate(idx):
            score = float(sims[row, j])
            out.append((names[j], score) if score >= 0.22 else ("Other", score))
        return out


def categorize_pending(zen, embedder=None, progress=None) -> int:
    """Categorize all visits that don't have a category yet. Returns count."""
    from .text import emotion
    db = zen.db
    semantic = embedder is not None and getattr(embedder, "semantic", False)
    # rows the lexical fallback couldn't place get another chance once a semantic model is available
    rows = db.q("SELECT id, url, domain, title FROM visits WHERE category IS NULL"
                + (" OR cat_method='none'" if semantic else ""))
    if not rows:
        return 0
    cat = Categorizer(embedder)
    memo: dict[tuple, tuple] = {}
    updates, leftovers = [], []
    for row in rows:
        url, domain, title = row["url"] or "", row["domain"] or "", row["title"] or ""
        key = (domain, title, url.split("?")[0][:120])
        if key not in memo:
            memo[key] = cat.rule_based(url, domain, title)
        result = memo[key]
        emo, val = emotion(title)
        if result is None:
            leftovers.append((row["id"], title, emo, val))
        else:
            updates.append((result[0], result[1], result[2], emo, val, row["id"]))
    if leftovers:
        titles = [t or "" for _, t, _, _ in leftovers]
        sem = cat.semantic(titles) if cat.embedder else [("Other", 0.0)] * len(titles)
        for (vid, _t, emo, val), (name, score) in zip(leftovers, sem):
            updates.append((name, round(score, 3), "semantic" if cat.embedder and name != "Other" else "none",
                            emo, val, vid))
    db.many("UPDATE visits SET category=?, category_conf=?, cat_method=?, emotion=?, valence=? WHERE id=?", updates)
    if progress:
        progress(f"> categorized {len(updates)} visits ({len(leftovers)} needed the fallback)")
    return len(updates)


def cosine_top(vec: np.ndarray, matrix: np.ndarray, k: int = 5) -> list[int]:
    sims = matrix @ vec
    return list(np.argsort(-sims)[:k])
