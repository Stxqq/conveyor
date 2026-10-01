from __future__ import annotations

import dataclasses
import hashlib
import json
import pickle
from typing import Any

import numpy as np

# Bump when the hashing or on-disk formats change so old cache entries miss.
FORMAT_VERSION = "1"


def digest(value: Any) -> str:
    """Content hash of a step output.

    Hashes structure rather than serialized bytes: ``np.savez`` stamps zip
    entries with the current time, so the same array never produces the same
    file twice.
    """
    h = hashlib.sha256()
    _feed(h, value)
    return h.hexdigest()


def _feed(h: Any, value: Any) -> None:
    if value is None or isinstance(value, (bool, int, float, str)):
        h.update(f"{type(value).__name__}:{value!r};".encode())
    elif isinstance(value, bytes):
        h.update(b"bytes:%d:" % len(value))
        h.update(value)
    elif isinstance(value, np.ndarray) and value.dtype != object:
        h.update(f"nd:{value.dtype.str}:{value.shape};".encode())
        h.update(np.ascontiguousarray(value).tobytes())
    elif isinstance(value, np.generic):
        h.update(f"np:{value.dtype.str}:{value.item()!r};".encode())
    elif isinstance(value, dict):
        h.update(b"{")
        # repr so dicts with mixed key types still sort
        for key in sorted(value, key=repr):
            _feed(h, key)
            _feed(h, value[key])
        h.update(b"}")
    elif isinstance(value, (list, tuple)):
        h.update(b"[" if isinstance(value, list) else b"(")
        for item in value:
            _feed(h, item)
        h.update(b"]")
    elif isinstance(value, (set, frozenset)):
        # pickling a set follows iteration order, which changes with the
        # per-process string hash seed; sort the member digests instead
        h.update(b"set{")
        for member in sorted(digest(v) for v in value):
            h.update(member.encode())
        h.update(b"}")
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        h.update(f"dc:{cls.__module__}.{cls.__qualname__}".encode())
        for f in dataclasses.fields(value):
            _feed(h, f.name)
            _feed(h, getattr(value, f.name))
    else:
        h.update(b"pickle:")
        h.update(pickle.dumps(value, protocol=5))


def cache_key(
    step_name: str,
    fingerprint: str,
    version: str,
    params: dict[str, Any],
    inputs: dict[str, str],
) -> str:
    """Key for a step execution: its code, the params it reads and the
    content hashes of the artifacts it consumes."""
    payload = {
        "format": FORMAT_VERSION,
        "step": step_name,
        "code": fingerprint,
        "version": version,
        "params": {k: digest(v) for k, v in sorted(params.items())},
        "inputs": dict(sorted(inputs.items())),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
