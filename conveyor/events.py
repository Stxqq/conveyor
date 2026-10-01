from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

Listener = Callable[[dict[str, Any]], None]


class EventLog:
    """Append-only JSON-lines stream of everything that happens in a run.

    The UI tails this file for live runs and replays it for finished ones,
    so every event is self-contained and carries a sequence number.
    """

    def __init__(self, path: Path, listeners: list[Listener] | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._seq = 0
        self._listeners = list(listeners or [])

    def emit(self, kind: str, /, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "ts": round(time.time(), 4), "type": kind}
            event.update(_finite(fields))
            self._fh.write(json.dumps(event, default=str) + "\n")
            self._fh.flush()
            for listener in self._listeners:
                listener(event)
        return event

    def close(self) -> None:
        self._fh.close()


def read_events(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def follow(
    path: Path, poll: float = 0.1, idle_timeout: float = 600.0
) -> Iterator[dict[str, Any]]:
    """Yield events as they're appended, stopping after ``run_finished``."""
    deadline = time.monotonic() + idle_timeout
    pending = ""
    with path.open(encoding="utf-8") as fh:
        while time.monotonic() < deadline:
            chunk = fh.readline()
            if not chunk:
                time.sleep(poll)
                continue
            pending += chunk
            if not pending.endswith("\n"):
                continue
            event = json.loads(pending)
            pending = ""
            deadline = time.monotonic() + idle_timeout
            yield event
            if event["type"] == "run_finished":
                return


def _finite(value: Any) -> Any:
    # NaN and inf aren't valid JSON and the browser's JSON.parse rejects them.
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value
