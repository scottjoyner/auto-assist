"""Lightweight vector memory for the swarm.

Embeds the knowledge vault (and, later, hermes session transcripts and the
model-profiles registry) so self-tasks and real tasks can pull *relevant*
context via semantic search instead of a blind top-level snapshot.

Embeddings come from the fleet embed model (``text-embedding-nomic-embed-text-v1.5``)
through the router's OpenAI-compatible ``/v1/embeddings`` endpoint. The index is
persisted as JSON under ``<KNOWLEDGE_ROOT>/.memory/index.json`` so rebuilds are
cheap and the module degrades gracefully (returns ``None``) when the embed model
is not loaded — callers should fall back to ``_gather_knowledge_context``.

Usage:
    python swarm_memory.py index          # (re)build the vault index
    python swarm_memory.py search "query" # print top-5 chunks
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

KNOWLEDGE_ROOT = os.getenv("HERMES_KNOWLEDGE_ROOT", "/root/knowledge")
from assistx.knowledge_roots import (
    resolve_knowledge_roots, safe_knowledge_files, safe_read_text, checked_write_path,
)
KNOWLEDGE_READ_ROOT, KNOWLEDGE_WRITE_ROOT, KNOWLEDGE_SPLIT_ENABLED = (
    resolve_knowledge_roots(
        KNOWLEDGE_ROOT,
        os.getenv("HERMES_EVAL_PATH", "/root/knowledge/model-profiles.json"),
    )
)
EMBED_MODEL = os.getenv("HERMES_EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5")
ROUTER_EMBED_URL = os.getenv("HERMES_ROUTER_EMBED_URL", "http://host.docker.internal:8088/v1/embeddings")
MEMORY_DIR = os.path.join(KNOWLEDGE_WRITE_ROOT, ".memory")
MEMORY_INDEX_PATH = os.path.join(MEMORY_DIR, "index.json")

_TEMPLATE_FILES = {"Home.md", "README.md", "VAULT_INDEX.md", "ENV.md"}

# If the embed endpoint is down/unavailable, remember it for a while so we don't
# burn a full timeout on every chunk of every self-task.
_embed_broken_until: float = 0.0
_EMBED_COOLDOWN_S = 300.0


def embed(text: str, timeout: int = 30) -> Optional[List[float]]:
    """Return the embedding vector for ``text`` or ``None`` on any failure.

    The embed model is slow (~5s) and occasionally exceeds a short timeout under
    load. Retries briefly on transient failures (429 / timeout). Only a hard
    connection failure disables embeddings fleet-wide (cooldown) — timeouts and
    429s are transient and must not kill a bulk build.
    """
    global _embed_broken_until
    if requests is None or time.time() < _embed_broken_until:
        return None
    # The internal service identity is never quota/claim-scoped. Reject
    # embedding requests without a specifically scoped executor JWT before
    # making any HTTP call or changing backoff state. Decoding only checks
    # shape and expected model; the router MUST authenticate signature,
    # owner, expiry, spend fence and revocation on the actual request.
    claim_token = os.getenv("HERMES_EXECUTOR_TOKEN", "").strip()
    if claim_token.count(".") != 2:
        return None
    try:
        payload = claim_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(
            payload + "=" * (-len(payload) % 4)
        ))
        allowed = claims.get("allowed_model_aliases")
        if not isinstance(allowed, list) or EMBED_MODEL not in allowed or not all(
            isinstance(alias, str) and alias.strip() for alias in allowed
        ):
            return None
    except (ValueError, TypeError, AttributeError, UnicodeError, binascii.Error):
        return None
    headers = {"Authorization": f"Bearer {claim_token}"}
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(
                ROUTER_EMBED_URL,
                json={"model": EMBED_MODEL, "input": text},
                timeout=timeout,
                headers=headers,
            )
            if resp.status_code == 200:
                return resp.json().get("data", [{}])[0].get("embedding")
            last_err = resp.status_code
            if resp.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            return None  # 4xx other than 429: bad input, not retryable
        except requests.Timeout:
            last_err = "timeout"
            time.sleep(1.0 * (attempt + 1))
            continue
        except requests.ConnectionError:
            _embed_broken_until = time.time() + _EMBED_COOLDOWN_S
            return None
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(1.0 * (attempt + 1))
            continue
    return None


def _norm(v: List[float]) -> float:
    return math.sqrt(sum(x * x for x in v)) or 1.0


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (_norm(a) * _norm(b))


def _chunk_text(text: str, max_chars: int = 900) -> List[str]:
    """Split markdown into heading/paragraph-ish chunks bounded by max_chars."""
    chunks: List[str] = []
    buf: List[str] = []
    size = 0
    for line in text.splitlines():
        buf.append(line)
        size += len(line) + 1
        if size >= max_chars and buf:
            chunks.append("\n".join(buf).strip())
            buf, size = [], 0
    if buf:
        chunks.append("\n".join(buf).strip())
    return [c for c in chunks if c]


class MemoryIndex:
    def __init__(self, path: str = MEMORY_INDEX_PATH):
        self.path = path
        self.items: List[Dict[str, Any]] = []
        self.built_at: float = 0.0
        self.load()

    def load(self) -> None:
        if os.getenv("HERMES_REQUIRE_SIGNED_CORPUS", "") == "true":
            raise ValueError("signed corpus mode forbids unbound persisted semantic index")
        try:
            if KNOWLEDGE_SPLIT_ENABLED:
                checked_write_path(KNOWLEDGE_WRITE_ROOT, self.path)
            with open(self.path, "r", errors="ignore") as fh:
                d = json.load(fh)
            self.items = d.get("items", [])
            self.built_at = d.get("built_at", 0.0)
        except (OSError, ValueError):
            self.items, self.built_at = [], 0.0

    def save(self) -> None:
        if KNOWLEDGE_SPLIT_ENABLED:
            checked_write_path(KNOWLEDGE_WRITE_ROOT, self.path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if KNOWLEDGE_SPLIT_ENABLED:
            checked_write_path(KNOWLEDGE_WRITE_ROOT, self.path)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
            stream = os.fdopen(fd, "w")
        else:
            stream = open(self.path, "w")
        with stream as fh:
            json.dump({"built_at": self.built_at, "items": self.items}, fh)

    def add(self, text: str, meta: Dict[str, Any]) -> bool:
        vec = embed(text)
        if not vec:
            return False
        self.items.append({"text": text, "vec": vec, "meta": meta})
        return True

    def search(self, query: str, k: int = 5) -> List[Tuple[str, Dict[str, Any]]]:
        qv = embed(query)
        if not qv or not self.items:
            return []
        scored = [(cosine(qv, it["vec"]), it["text"], it["meta"]) for it in self.items]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(t, m) for _, t, m in scored[:k]]

    def index_vault(self, max_chars: int = 900) -> int:
        if os.getenv("HERMES_REQUIRE_SIGNED_CORPUS", "") == "true":
            raise ValueError("signed corpus mode forbids unbound semantic index builds")
        # Accumulate into the existing index rather than wiping it, so an
        # interrupted/re-run build keeps whatever was already embedded.
        global _embed_broken_until
        _embed_broken_until = 0.0  # a build run should never be blocked by cooldown
        count = 0
        self.load()  # pick up anything a prior (partial) run already persisted
        existing_sources = {it.get("meta", {}).get("source") for it in self.items}
        if not os.path.isdir(KNOWLEDGE_READ_ROOT):
            return 0
        for fp in safe_knowledge_files(KNOWLEDGE_READ_ROOT, _TEMPLATE_FILES):
            try:
                text = safe_read_text(fp, 200000)
            except (OSError, ValueError):
                continue
            rel = os.path.relpath(fp, KNOWLEDGE_READ_ROOT)
            if rel in existing_sources:
                continue  # already indexed; skip to avoid re-embedding
            for chunk in _chunk_text(text, max_chars):
                if self.add(chunk, {"source": rel}):
                    count += 1
                    if count % 15 == 0:
                        self.save()  # persist incrementally so a slow build survives
                time.sleep(0.15)  # throttle to avoid embed-endpoint rate limits
        self.built_at = time.time()
        self.save()
        return count


def vault_semantic_context(query: str, k: int = 5, fallback: str = "") -> str:
    """Top-k relevant vault chunks for ``query``; falls back to ``fallback``.

    This only *queries* a prebuilt index — it never builds it on the fly (a full
    build is slow and must run as a scheduled/background job). If the index is
    empty or the embed model is down, callers fall back to the snapshot.
    """
    if os.getenv("HERMES_REQUIRE_SIGNED_CORPUS", "") == "true":
        raise ValueError("signed corpus mode forbids stale semantic index reads")
    if time.time() < _embed_broken_until:
        return fallback
    idx = MemoryIndex()
    if not idx.items:
        return fallback
    results = idx.search(query, k)
    if not results:
        return fallback
    return "\n\n".join(f"# {meta.get('source', '')}\n{text}" for text, meta in results)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "search"
    if cmd == "index":
        n = MemoryIndex().index_vault()
        print(f"indexed {n} chunks -> {MEMORY_INDEX_PATH}")
    elif cmd == "search":
        q = " ".join(sys.argv[2:]) or "swarm architecture"
        for text, meta in MemoryIndex().search(q, 5):
            print(f"[{meta.get('source')}] {text[:160]}\n---")
    else:
        print("usage: swarm_memory.py [index|search '<query>']")
