"""Fail-closed source-window proofs for the isolated trace collector candidate.

A receipt proves the pinned, original source prefix only, not archive durability.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class SourceWindowHold(ValueError):
    """Operator review required: never rewind a cursor automatically."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture_window(path: Path, previous: dict | None):
    """Read a pinned original source and verify previous committed prefix."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        pinned = before.st_size
        if pinned < 0:
            raise SourceWindowHold("negative source size")
        blocks = bytearray()
        while len(blocks) < pinned:
            piece = os.read(fd, min(1 << 20, pinned - len(blocks)))
            if not piece:
                raise SourceWindowHold("source shortened during read")
            blocks.extend(piece)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns):
            raise SourceWindowHold("source modified while snapshotting")
    finally:
        os.close(fd)
    raw = bytes(blocks)
    complete_end = raw.rfind(b"\n") + 1
    if previous is not None:
        if type(previous) is not dict:
            raise SourceWindowHold("non-object cursor")
        fields = ("offset", "source_dev", "source_inode", "prefix_sha256")
        if any(key not in previous for key in fields):
            raise SourceWindowHold("legacy cursor missing source identity: migration review")
        if any(type(previous[key]) is not int or previous[key] < 0
               for key in fields[:-1]):
            raise SourceWindowHold("invalid cursor provenance")
        if type(previous["prefix_sha256"]) is not str:
            raise SourceWindowHold("invalid prefix checksum")
        if (previous["source_dev"], previous["source_inode"]) != (
             before.st_dev, before.st_ino):
            raise SourceWindowHold("source generation changed")
        offset = previous["offset"]
        if offset > complete_end:
            raise SourceWindowHold("cursor past last complete record")
        if offset and raw[offset - 1] != 10:
            raise SourceWindowHold("cursor inside a record")
        if _digest(raw[:offset]) != previous["prefix_sha256"]:
            raise SourceWindowHold("committed source prefix changed")
    else:
        offset = 0
    payload = raw[offset:complete_end]
    state = {
        "offset": complete_end,
        "source_dev": before.st_dev,
        "source_inode": before.st_ino,
        "prefix_sha256": _digest(raw[:complete_end]),
    }
    evidence = {
        "offset_from": offset,
        "offset_end_committed": complete_end,
        "size_at_open": pinned,
        "source_dev": before.st_dev,
        "source_inode": before.st_ino,
        "prefix_sha256": state["prefix_sha256"],
        "payload_sha256": _digest(payload),
        "torn_tail_dropped": pinned != complete_end,
    }
    return payload, state, evidence
