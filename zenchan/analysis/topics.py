"""Unsupervised interest discovery and the semantic map.

Pipeline: embed every distinct page title (cached in SQLite) -> k-means ->
name each cluster with class-based TF-IDF keywords (the BERTopic trick) ->
project to 2D/3D with UMAP when installed, otherwise PCA.
"""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from .text import brand, tokens

MAX_TITLES = 12_000
SITE_SUFFIX = re.compile(r"\s*[-|–—:•]\s*[^-|–—:•]{2,40}$")


def topic_text(title: str) -> str:
    """Drop the trailing ' - Site Name' so clusters are about content, not sites."""
    t = title or ""
    stripped = SITE_SUFFIX.sub("", t)
    return stripped if len(stripped) >= 8 else t


def embed_text(title: str, domain: str, embedder) -> str:
    text = topic_text(title)
    if not getattr(embedder, "semantic", False):
        site = brand(domain or "")
        text = f"{text} {site} {site}"   # lexical vectors need the site as shared context
    return text


def kmeans(X: np.ndarray, k: int, iters: int = 30, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = len(X)
    k = max(1, min(k, n))
    centers = [X[rng.integers(n)]]
    d2 = np.full(n, np.inf)
    for _ in range(1, k):                                  # k-means++ seeding
        d2 = np.minimum(d2, ((X - centers[-1]) ** 2).sum(axis=1))
        total = d2.sum()
        centers.append(X[rng.choice(n, p=d2 / total)] if total > 0 else X[rng.integers(n)])
    C = np.array(centers)
    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        dist = (X ** 2).sum(1)[:, None] - 2 * X @ C.T + (C ** 2).sum(1)[None, :]
        new = dist.argmin(axis=1)
        if np.array_equal(new, labels) and _ > 0:
            break
        labels = new
        for j in range(k):
            members = X[labels == j]
            if len(members):
                C[j] = members.mean(axis=0)
    return labels, C


def ctfidf_labels(docs_by_cluster: dict[int, list[tuple[str, float]]], top: int = 4) -> dict[int, list[str]]:
    tf: dict[int, Counter] = {}
    total = Counter()
    for c, docs in docs_by_cluster.items():
        cnt = Counter()
        for text, weight in docs:
            for tok in set(tokens(text)):
                cnt[tok] += weight
        tf[c] = cnt
        total.update(cnt)
    avg = sum(total.values()) / max(len(tf), 1)
    out = {}
    for c, cnt in tf.items():
        scored = {t: f * math.log(1 + avg / total[t]) for t, f in cnt.items() if not t.isdigit()}
        out[c] = [t for t, _ in sorted(scored.items(), key=lambda kv: -kv[1])[:top]]
    return out


def project(X: np.ndarray, dims: int, seed: int = 0) -> np.ndarray:
    if len(X) >= 60:
        try:
            import umap  # optional
            return umap.UMAP(n_components=dims, n_neighbors=15, min_dist=0.1, metric="cosine",
                             random_state=seed).fit_transform(X)
        except Exception:
            pass
    Xc = X - X.mean(axis=0)
    _, _, vt = np.linalg.svd(Xc, full_matrices=False)
    P = Xc @ vt[:dims].T
    P += np.random.default_rng(seed).normal(0, 0.02 * (P.std() or 1), P.shape)  # de-stack duplicates
    return P


def build(zen, embedder, progress=None) -> dict:
    db = zen.db
    rows = db.q("""SELECT title_key, MAX(title) title, MAX(domain) domain, SUM(duration) dur, COUNT(*) n,
                          MAX(category) category
                   FROM visits WHERE title_key IS NOT NULL AND title != ''
                   GROUP BY title_key ORDER BY dur DESC LIMIT ?""", (MAX_TITLES,))
    if len(rows) < 8:
        return {"topics": 0}
    keys = [r["title_key"] for r in rows]
    cached = {r["key"]: r["vec"] for r in db.q("SELECT key, vec FROM titles WHERE model=?", (embedder.name,))}
    missing = [r for r in rows if r["title_key"] not in cached]
    if missing:
        if progress:
            progress(f"> embedding {len(missing)} new titles with {embedder.name}")
        for start in range(0, len(missing), 512):
            chunk = missing[start:start + 512]
            vecs = embedder.encode([embed_text(r["title"], r["domain"], embedder) for r in chunk])
            db.many("""INSERT INTO titles(key, title, domain, model, vec) VALUES (?,?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET model=excluded.model, vec=excluded.vec,
                       title=excluded.title, domain=excluded.domain""",
                    [(r["title_key"], r["title"], r["domain"], embedder.name, v.astype(np.float32).tobytes())
                     for r, v in zip(chunk, vecs)])
            for r, v in zip(chunk, vecs):
                cached[r["title_key"]] = v.astype(np.float32).tobytes()
    E = np.vstack([np.frombuffer(cached[k], dtype=np.float32) for k in keys])
    cats = sorted({r["category"] or "Other" for r in rows})
    onehot = np.zeros((len(rows), len(cats)), dtype=np.float32)
    for i, r in enumerate(rows):
        onehot[i, cats.index(r["category"] or "Other")] = 1.0
    semantic = getattr(embedder, "semantic", False)
    # Unrelated unit vectors sit ~1.41 apart. For semantic embeddings a light category hint (0.35)
    # just stabilizes clusters; lexical hash vectors carry little similarity, so there the
    # category must dominate (1.5 -> 2.1 apart) and keywords then name sub-themes within it.
    cat_weight = 0.35 if semantic else 1.5
    X = np.hstack([E, cat_weight * onehot])
    k = round(math.sqrt(len(X) / 2)) if semantic else max(round(math.sqrt(len(X) / 2)), len(cats))
    k = int(np.clip(k, 6, 30))
    labels, _ = kmeans(X, k)
    weights = np.log1p(np.array([r["dur"] or 0 for r in rows], dtype=float) / 60) + 0.1
    docs: dict[int, list] = {}
    meta: dict[int, dict] = {}
    for r, lab, w in zip(rows, labels, weights):
        docs.setdefault(int(lab), []).append((topic_text(r["title"]), float(w)))
        m = meta.setdefault(int(lab), {"size": 0, "minutes": 0.0, "cats": Counter()})
        m["size"] += 1
        m["minutes"] += (r["dur"] or 0) / 60
        m["cats"][r["category"] or "Other"] += r["dur"] or 0
    words = ctfidf_labels(docs)
    if progress:
        progress(f"> projecting {len(X)} titles into the semantic map")
    p2, p3 = project(X, 2), project(X, 3)
    with db.write_lock:
        conn = db.connect()
        conn.execute("DELETE FROM topics")
        conn.executemany("INSERT INTO topics(id, label, keywords, size, minutes, category) VALUES (?,?,?,?,?,?)",
                         [(c, " · ".join(words.get(c, [])[:3]) or f"topic {c}", ",".join(words.get(c, [])),
                           m["size"], round(m["minutes"], 1), m["cats"].most_common(1)[0][0])
                          for c, m in meta.items()])
        conn.executemany("UPDATE titles SET topic=?, x2=?, y2=?, x3=?, y3=?, z3=? WHERE key=?",
                         [(int(lab), float(a[0]), float(a[1]), float(b[0]), float(b[1]), float(b[2]), key)
                          for key, lab, a, b in zip(keys, labels, p2, p3)])
        conn.commit()
    return {"topics": len(meta), "titles": len(X), "embedder": embedder.name}
