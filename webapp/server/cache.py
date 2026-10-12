"""Tiny in-process cache keyed by snapshot version.

The snapshot file only changes when the pipeline republishes it; its mtime is
part of every key, so a new snapshot is served on the very next request and the
TTL only bounds memory.
"""

import time

from . import db

TTL_SECONDS = 300
MAX_ENTRIES = 512
_store: dict[tuple, tuple[float, object]] = {}


def _key(name: str, args: tuple) -> tuple:
    frozen = tuple(tuple(sorted(a.items())) if isinstance(a, dict) else a for a in args)
    return (db.snapshot_version(), name, frozen)


def cached(name: str, fn, *args):
    key = _key(name, args)
    hit = _store.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < TTL_SECONDS:
        return hit[1]
    value = fn(*args)
    if len(_store) >= MAX_ENTRIES:
        _store.clear()
    _store[key] = (now, value)
    return value


def clear() -> None:
    _store.clear()
