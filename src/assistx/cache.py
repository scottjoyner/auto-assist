
from __future__ import annotations
import sqlite3, os, hashlib, json
from typing import Optional
from .config import settings

def _ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT, created_at INTEGER DEFAULT (strftime('%s','now')))")
    conn.commit()

def cache_get(key: str) -> Optional[str]:
    path = settings.cache_path
    conn = sqlite3.connect(path)
    try:
        _ensure(conn)
        cur = conn.execute("SELECT v FROM cache WHERE k=?", (key,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()

def cache_set(key: str, value: str):
    path = settings.cache_path
    conn = sqlite3.connect(path)
    try:
        _ensure(conn)
        conn.execute("INSERT OR REPLACE INTO cache (k, v) VALUES (?,?)", (key, value))
        conn.commit()
    finally:
        conn.close()

def make_key(model: str, prompt: str, mode: str="text") -> str:
    h = hashlib.sha256((model + "|" + mode + "|" + prompt).encode("utf-8")).hexdigest()
    return f"{mode}:{model}:{h}"
