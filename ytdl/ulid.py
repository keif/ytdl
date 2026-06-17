"""Monotonic ULID generator.

Crockford base32, 48 bits of millis + 80 bits of randomness. When called more
than once in the same millisecond, the random part is incremented to preserve
sort order rather than risk a collision.
"""
from __future__ import annotations

import os
import threading
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_lock = threading.Lock()
_last_ms = 0
_last_rand = 0


def _b32(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_ulid() -> str:
    global _last_ms, _last_rand
    with _lock:
        ms = max(int(time.time() * 1000), _last_ms)
        if ms == _last_ms:
            _last_rand += 1
        else:
            _last_ms = ms
            _last_rand = int.from_bytes(os.urandom(10), "big")
        rand = _last_rand & ((1 << 80) - 1)
    return _b32(ms, 10) + _b32(rand, 16)
