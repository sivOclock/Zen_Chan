"""Text embeddings with graceful degradation.

* ``minilm`` — a sentence-transformer (all-MiniLM-L6-v2, a 6-layer transformer)
  giving real semantic similarity. Needs ``pip install zenchan[ml]``.
* ``hash``   — a dependency-free hashed bag of words + bigrams. Lexical only,
  but deterministic and instant, so it runs on anything (Raspberry Pi, Termux).
"""

from __future__ import annotations

import hashlib
import logging
import os

import numpy as np

from .text import tokens

log = logging.getLogger(__name__)


class HashEmbedder:
    name = "hash-v2"
    semantic = False
    dim = 512

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            words = tokens(text)
            feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
            for feat in feats:
                h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
                out[row, h % self.dim] += 1.0 if (h >> 40) & 1 else -1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


class MiniLMEmbedder:
    name = "minilm-l6-v2"
    semantic = True
    dim = 384
    model_id = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self):
        from sentence_transformers import SentenceTransformer  # noqa: WPS433 (optional dependency)
        local = os.environ.get("ZENCHAN_MODEL_DIR")
        self.model = SentenceTransformer(local or self.model_id, device="cpu")

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, batch_size=64, show_progress_bar=False,
                                 normalize_embeddings=True, convert_to_numpy=True)
        return vecs.astype(np.float32)


_CACHE: dict[str, object] = {}


def get_embedder(preference: str = "auto"):
    """Best available embedder; never raises."""
    if preference in _CACHE:
        return _CACHE[preference]
    embedder = None
    if preference in ("auto", "minilm"):
        try:
            embedder = MiniLMEmbedder()
        except Exception as exc:  # ImportError, offline model download, etc.
            if preference == "minilm":
                log.warning("MiniLM unavailable (%s); falling back to hashing embedder", exc)
            embedder = None
    embedder = embedder or HashEmbedder()
    _CACHE[preference] = embedder
    return embedder


def available_backends() -> dict:
    """Which optional ML/LLM packages are installed (checked without importing them)."""
    from importlib.util import find_spec
    status = {"hash": True}
    for mod in ("sentence_transformers", "umap", "sklearn", "anthropic"):
        try:
            status[mod] = find_spec(mod) is not None
        except (ImportError, ValueError):
            status[mod] = False
    return status
