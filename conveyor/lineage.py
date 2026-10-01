from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    pipeline    TEXT NOT NULL,
    status      TEXT NOT NULL,
    params      TEXT NOT NULL,
    graph       TEXT NOT NULL,
    source      TEXT,
    started_at  REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS artifacts (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS step_runs (
    id          INTEGER PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id),
    step        TEXT NOT NULL,
    status      TEXT NOT NULL,
    cache_key   TEXT,
    cached_from INTEGER REFERENCES step_runs(id),
    attempts    INTEGER NOT NULL DEFAULT 0,
    started_at  REAL,
    finished_at REAL,
    error       TEXT,
    output      TEXT REFERENCES artifacts(id),
    UNIQUE (run_id, step)
);
CREATE TABLE IF NOT EXISTS step_inputs (
    step_run_id INTEGER NOT NULL REFERENCES step_runs(id),
    name        TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    PRIMARY KEY (step_run_id, name)
);
CREATE TABLE IF NOT EXISTS metrics (
    step_run_id INTEGER NOT NULL REFERENCES step_runs(id),
    name        TEXT NOT NULL,
    value       REAL,
    PRIMARY KEY (step_run_id, name)
);
CREATE INDEX IF NOT EXISTS step_runs_cache ON step_runs(cache_key, status);
CREATE INDEX IF NOT EXISTS step_runs_output ON step_runs(output);
CREATE INDEX IF NOT EXISTS step_inputs_artifact ON step_inputs(artifact_id);
"""


class Lineage:
    """Run metadata and artifact lineage in one SQLite file.

    Successful step runs double as the cache index: a cache hit is the latest
    step run with the same key whose output is still on disk.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _write(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock, self._db:
            return self._db.execute(sql, tuple(args))

    def _rows(self, sql: str, args: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, tuple(args))]

    def start_run(
        self,
        run_id: str,
        pipeline: str,
        params: dict[str, Any],
        graph: list[dict[str, Any]],
        source: str | None,
        started_at: float,
    ) -> None:
        self._write(
            "INSERT INTO runs (id, pipeline, status, params, graph, source, started_at)"
            " VALUES (?, ?, 'running', ?, ?, ?, ?)",
            (
                run_id,
                pipeline,
                json.dumps(params, default=repr),
                json.dumps(graph),
                source,
                started_at,
            ),
        )

    def finish_run(self, run_id: str, status: str, finished_at: float) -> None:
        self._write(
            "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
            (status, finished_at, run_id),
        )

    def add_artifact(self, artifact_id: str, kind: str, size: int, at: float) -> None:
        self._write(
            "INSERT OR IGNORE INTO artifacts (id, kind, size, created_at)"
            " VALUES (?, ?, ?, ?)",
            (artifact_id, kind, size, at),
        )

    def record_step(
        self,
        run_id: str,
        step: str,
        status: str,
        *,
        cache_key: str | None = None,
        cached_from: int | None = None,
        attempts: int = 0,
        started_at: float | None = None,
        finished_at: float | None = None,
        error: str | None = None,
        output: str | None = None,
        inputs: dict[str, str] | None = None,
        metrics: dict[str, float | None] | None = None,
    ) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO step_runs (run_id, step, status, cache_key, cached_from,"
                " attempts, started_at, finished_at, error, output)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    step,
                    status,
                    cache_key,
                    cached_from,
                    attempts,
                    started_at,
                    finished_at,
                    error,
                    output,
                ),
            )
            step_run_id = cur.lastrowid
            self._db.executemany(
                "INSERT INTO step_inputs VALUES (?, ?, ?)",
                [(step_run_id, k, v) for k, v in (inputs or {}).items()],
            )
            self._db.executemany(
                "INSERT INTO metrics VALUES (?, ?, ?)",
                [(step_run_id, k, v) for k, v in (metrics or {}).items()],
            )
        assert step_run_id is not None
        return step_run_id

    def cached(self, cache_key: str) -> dict[str, Any] | None:
        """Most recent successful execution for this key (not a cache hit itself)."""
        rows = self._rows(
            "SELECT s.id, s.output, a.kind, s.run_id FROM step_runs s"
            " JOIN artifacts a ON a.id = s.output"
            " WHERE s.cache_key = ? AND s.status = 'succeeded'"
            " ORDER BY s.id DESC LIMIT 1",
            (cache_key,),
        )
        return rows[0] if rows else None

    def metrics_of(self, step_run_id: int) -> dict[str, float | None]:
        rows = self._rows(
            "SELECT name, value FROM metrics WHERE step_run_id = ?", (step_run_id,)
        )
        return {r["name"]: r["value"] for r in rows}

    def runs(self, limit: int = 20, pipeline: str | None = None) -> list[dict]:
        where = "WHERE r.pipeline = ?" if pipeline else ""
        args: list[Any] = [pipeline] if pipeline else []
        rows = self._rows(
            "SELECT r.*,"
            " SUM(s.status = 'succeeded') AS succeeded,"
            " SUM(s.status = 'cached') AS cached,"
            " SUM(s.status = 'failed') AS failed,"
            " SUM(s.status = 'skipped') AS skipped"
            f" FROM runs r LEFT JOIN step_runs s ON s.run_id = r.id {where}"
            " GROUP BY r.id ORDER BY r.started_at DESC LIMIT ?",
            [*args, limit],
        )
        return [_decode_run(r) for r in rows]

    def resolve_run(self, ref: str) -> str | None:
        """Accept a full id, a unique prefix, or ``latest``."""
        if ref == "latest":
            rows = self._rows("SELECT id FROM runs ORDER BY started_at DESC LIMIT 1")
        else:
            rows = self._rows(
                "SELECT id FROM runs WHERE id = ? OR id LIKE ?"
                " ORDER BY started_at DESC",
                (ref, ref.replace("%", "") + "%"),
            )
            if len(rows) > 1 and rows[0]["id"] != ref:
                raise LookupError(f"run prefix {ref!r} is ambiguous")
        return rows[0]["id"] if rows else None

    def run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not rows:
            return None
        run = _decode_run(rows[0])
        steps = self._rows(
            "SELECT s.*, a.kind, a.size FROM step_runs s"
            " LEFT JOIN artifacts a ON a.id = s.output"
            " WHERE s.run_id = ? ORDER BY s.id",
            (run_id,),
        )
        for s in steps:
            s["metrics"] = self.metrics_of(s["id"])
            s["inputs"] = {
                r["name"]: r["artifact_id"]
                for r in self._rows(
                    "SELECT name, artifact_id FROM step_inputs WHERE step_run_id = ?",
                    (s["id"],),
                )
            }
        run["steps"] = steps
        return run

    def producer(self, artifact_id: str) -> dict[str, Any] | None:
        """The step run that actually computed this artifact (not a cache hit)."""
        rows = self._rows(
            "SELECT * FROM step_runs WHERE output = ? AND status = 'succeeded'"
            " ORDER BY id LIMIT 1",
            (artifact_id,),
        )
        return rows[0] if rows else None

    def consumers(self, artifact_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT DISTINCT s.run_id, s.step, i.name AS as_input FROM step_inputs i"
            " JOIN step_runs s ON s.id = i.step_run_id WHERE i.artifact_id = ?"
            " ORDER BY s.id",
            (artifact_id,),
        )

    def upstream(self, artifact_id: str) -> list[dict[str, Any]]:
        """Every artifact this one was derived from, nearest first, with the
        step that produced it."""
        seen = {artifact_id}
        frontier = [artifact_id]
        found: list[dict[str, Any]] = []
        depth = 0
        while frontier:
            depth += 1
            marks = ",".join("?" * len(frontier))
            rows = self._rows(
                "SELECT DISTINCT i.artifact_id, i.name FROM step_inputs i"
                " JOIN step_runs s ON s.id = i.step_run_id"
                f" WHERE s.output IN ({marks}) ORDER BY i.name",
                frontier,
            )
            frontier = []
            for r in rows:
                if r["artifact_id"] in seen:
                    continue
                seen.add(r["artifact_id"])
                frontier.append(r["artifact_id"])
                found.append(
                    {"artifact_id": r["artifact_id"], "step": r["name"], "depth": depth}
                )
        return found


def _decode_run(row: dict[str, Any]) -> dict[str, Any]:
    row["params"] = json.loads(row["params"])
    row["graph"] = json.loads(row["graph"])
    if row.get("finished_at") and row.get("started_at"):
        row["duration"] = row["finished_at"] - row["started_at"]
    return row
