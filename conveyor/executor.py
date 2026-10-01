from __future__ import annotations

import contextvars
import os
import random
import secrets
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import StepContext, _current
from .dag import Pipeline
from .events import EventLog, Listener
from .hashing import cache_key
from .lineage import Lineage
from .step import Step
from .store import Artifact, ArtifactStore

DONE = ("succeeded", "cached")


class StepTimeout(Exception):
    pass


@dataclass
class StepOutcome:
    name: str
    status: str
    attempts: int = 0
    duration: float = 0.0
    cache_key: str | None = None
    artifact: Artifact | None = None
    error: str | None = None
    metrics: dict[str, float | None] = field(default_factory=dict)


@dataclass
class RunResult:
    id: str
    pipeline: str
    params: dict[str, Any]
    status: str
    steps: dict[str, StepOutcome]
    started_at: float
    finished_at: float
    events_path: Path
    store: ArtifactStore = field(repr=False)

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    @property
    def duration(self) -> float:
        return self.finished_at - self.started_at

    def output(self, step: str) -> Any:
        """Load the artifact a step produced (or was served from cache)."""
        art = self.steps[step].artifact
        if art is None:
            raise KeyError(f"step {step!r} has no output ({self.steps[step].status})")
        return self.store.get(art.id, art.kind)

    def count(self, status: str) -> int:
        return sum(s.status == status for s in self.steps.values())


@dataclass
class _Attempted:
    value: Any = None
    artifact: Artifact | None = None
    error: str | None = None
    attempts: int = 0
    duration: float = 0.0
    started_at: float = 0.0
    metrics: dict[str, float | None] = field(default_factory=dict)


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def backoff_delay(base: float, attempt: int, cap: float = 60.0) -> float:
    # Exponential with "equal jitter": half the window is fixed, half random,
    # so parallel steps hitting the same flaky service don't retry in lockstep.
    window = min(cap, base * 2 ** (attempt - 1))
    return window / 2 + random.uniform(0, window / 2)


class Executor:
    """Runs pipelines on a thread pool, with caching, retries and lineage."""

    def __init__(
        self,
        workspace: str | os.PathLike[str] = ".conveyor",
        *,
        max_workers: int | None = None,
        cache: bool = True,
        listeners: list[Listener] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.store = ArtifactStore(self.workspace)
        self.lineage = Lineage(self.workspace / "conveyor.db")
        # Steps are mostly numpy, which releases the GIL; more threads than
        # that mostly just hold more intermediate arrays in memory at once.
        self.max_workers = max_workers or min(8, os.cpu_count() or 4)
        self.cache = cache
        self.listeners = list(listeners or [])

    def run(
        self,
        pipeline: Pipeline,
        params: dict[str, Any] | None = None,
        *,
        source: str | None = None,
    ) -> RunResult:
        return _Run(self, pipeline, pipeline.resolve_params(params), source).execute()


class _Run:
    def __init__(
        self,
        ex: Executor,
        pipeline: Pipeline,
        params: dict[str, Any],
        source: str | None,
    ) -> None:
        self.ex = ex
        self.pipeline = pipeline
        self.params = params
        self.source = source
        self.id = new_run_id()
        self.events = EventLog(
            ex.workspace / "runs" / f"{self.id}.jsonl", listeners=ex.listeners
        )
        self.outcomes: dict[str, StepOutcome] = {}
        self.waiting = {n: set(pipeline.upstream(n)) for n in pipeline.order}
        self.consumers: dict[str, set[str]] = {n: set() for n in pipeline.order}
        for parent, child in pipeline.edges():
            self.consumers[parent].add(child)
        self.in_flight: set[str] = set()
        self.values: dict[str, Any] = {}
        self._values_lock = threading.Lock()

    def execute(self) -> RunResult:
        started = time.time()
        graph = self.pipeline.describe()
        self.ex.lineage.start_run(
            self.id, self.pipeline.name, self.params, graph, self.source, started
        )
        self.events.emit(
            "run_started",
            run_id=self.id,
            pipeline=self.pipeline.name,
            params=self.params,
            graph=graph,
            workers=self.ex.max_workers,
            cache=self.ex.cache,
            pid=os.getpid(),
        )
        status = "failed"
        pool = ThreadPoolExecutor(self.ex.max_workers, thread_name_prefix="conveyor")
        running: dict[Future[_Attempted], tuple[str, str, dict[str, str]]] = {}
        try:
            while True:
                for name, key, inputs in self._launchable():
                    fut = pool.submit(self._attempt, name, inputs)
                    running[fut] = (name, key, inputs)
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in done:
                    name, key, inputs = running.pop(fut)
                    self._settle(name, key, inputs, fut.result())
            ok = all(o.status in DONE for o in self.outcomes.values())
            status = "succeeded" if ok else "failed"
        except KeyboardInterrupt:
            status = "cancelled"
            raise
        finally:
            # On Ctrl-C don't wait for steps we couldn't interrupt anyway.
            pool.shutdown(wait=status != "cancelled", cancel_futures=True)
            finished = time.time()
            self.ex.lineage.finish_run(self.id, status, finished)
            self.events.emit(
                "run_finished",
                status=status,
                duration=round(finished - started, 4),
                counts={
                    s: sum(o.status == s for o in self.outcomes.values())
                    for s in ("succeeded", "cached", "failed", "skipped")
                },
            )
            self.events.close()
        return RunResult(
            id=self.id,
            pipeline=self.pipeline.name,
            params=self.params,
            status=status,
            steps={n: self.outcomes[n] for n in self.pipeline.order},
            started_at=started,
            finished_at=finished,
            events_path=self.events.path,
            store=self.ex.store,
        )

    def _launchable(self) -> list[tuple[str, str, dict[str, str]]]:
        """Steps whose inputs are all ready. Cache hits are settled on the spot,
        which can unblock more steps, so keep going until nothing changes."""
        launch: list[tuple[str, str, dict[str, str]]] = []
        queued: set[str] = set()
        progressed = True
        while progressed:
            progressed = False
            for name in self.pipeline.order:
                if (
                    name in self.outcomes
                    or name in self.in_flight
                    or name in queued
                    or self.waiting[name]
                ):
                    continue
                step = self.pipeline.steps[name]
                inputs = {
                    u: self.outcomes[u].artifact.id  # type: ignore[union-attr]
                    for u in self.pipeline.upstream(name)
                }
                key = cache_key(
                    name, step.fingerprint, step.version, self._params_for(name), inputs
                )
                if (
                    self.ex.cache
                    and step.cache
                    and self._serve_from_cache(name, key, inputs)
                ):
                    progressed = True
                    continue
                queued.add(name)
                launch.append((name, key, inputs))
        self.in_flight.update(queued)
        return launch

    def _params_for(self, name: str) -> dict[str, Any]:
        return {p: self.params[p] for p in self.pipeline.step_params(name)}

    def _serve_from_cache(self, name: str, key: str, inputs: dict[str, str]) -> bool:
        hit = self.ex.lineage.cached(key)
        if hit is None or not self.ex.store.exists(hit["output"], hit["kind"]):
            return False
        path = self.ex.store.path_for(hit["output"], hit["kind"])
        artifact = Artifact(hit["output"], hit["kind"], path.stat().st_size, path)
        metrics = self.ex.lineage.metrics_of(hit["id"])
        now = time.time()
        self.ex.lineage.record_step(
            self.id,
            name,
            "cached",
            cache_key=key,
            cached_from=hit["id"],
            started_at=now,
            finished_at=now,
            output=artifact.id,
            inputs=inputs,
            metrics=metrics,
        )
        self.outcomes[name] = StepOutcome(
            name, "cached", cache_key=key, artifact=artifact, metrics=metrics
        )
        self.events.emit(
            "step_cached",
            step=name,
            cache_key=key,
            artifact=artifact.id,
            kind=artifact.kind,
            size=artifact.size,
            source_run=hit["run_id"],
            metrics=metrics,
        )
        self._release(name)
        return True

    def _attempt(self, name: str, inputs: dict[str, str]) -> _Attempted:
        step = self.pipeline.steps[name]
        tried = _Attempted(started_at=time.time())
        t0 = time.perf_counter()
        try:
            kwargs = {u: self._value_of(u) for u in inputs}
            kwargs.update(self._params_for(name))
            tried.value = self._call_with_retries(step, kwargs, tried)
            tried.artifact = self.ex.store.put(tried.value)
        except Exception as exc:
            tried.error = "".join(
                traceback.format_exception_only(type(exc), exc)
            ).strip()
            self.events.emit(
                "step_traceback", step=name, traceback=_trim_traceback(exc)
            )
        tried.duration = time.perf_counter() - t0
        return tried

    def _call_with_retries(
        self, step: Step, kwargs: dict[str, Any], tried: _Attempted
    ) -> Any:
        while True:
            tried.attempts += 1
            # A fresh context per attempt: a timed-out attempt keeps running in
            # the background and must not log into the one that replaced it.
            ctx = StepContext(
                self.id, step.name, tried.attempts, self.ex.workspace, self.events
            )
            tried.metrics = ctx.metrics
            self.events.emit("step_started", step=step.name, attempt=tried.attempts)
            try:
                return _call(step, kwargs, ctx)
            except Exception as exc:
                if not isinstance(exc, step.retry_on) or tried.attempts > step.retries:
                    raise
                delay = backoff_delay(step.backoff, tried.attempts)
                self.events.emit(
                    "step_retry",
                    step=step.name,
                    attempt=tried.attempts,
                    error=f"{type(exc).__name__}: {exc}",
                    delay=round(delay, 3),
                )
                time.sleep(delay)

    def _settle(
        self, name: str, key: str, inputs: dict[str, str], tried: _Attempted
    ) -> None:
        self.in_flight.discard(name)
        finished = tried.started_at + tried.duration
        if tried.error is None:
            assert tried.artifact is not None
            art = tried.artifact
            self.ex.lineage.add_artifact(art.id, art.kind, art.size, finished)
            self.ex.lineage.record_step(
                self.id,
                name,
                "succeeded",
                cache_key=key,
                attempts=tried.attempts,
                started_at=tried.started_at,
                finished_at=finished,
                output=art.id,
                inputs=inputs,
                metrics=tried.metrics,
            )
            self.outcomes[name] = StepOutcome(
                name,
                "succeeded",
                attempts=tried.attempts,
                duration=tried.duration,
                cache_key=key,
                artifact=art,
                metrics=tried.metrics,
            )
            with self._values_lock:
                self.values[name] = tried.value
            self.events.emit(
                "step_succeeded",
                step=name,
                attempts=tried.attempts,
                duration=round(tried.duration, 4),
                cache_key=key,
                artifact=art.id,
                kind=art.kind,
                size=art.size,
            )
            self._release(name)
            return

        self.ex.lineage.record_step(
            self.id,
            name,
            "failed",
            cache_key=key,
            attempts=tried.attempts,
            started_at=tried.started_at,
            finished_at=finished,
            error=tried.error,
            inputs=inputs,
            metrics=tried.metrics,
        )
        self.outcomes[name] = StepOutcome(
            name,
            "failed",
            attempts=tried.attempts,
            duration=tried.duration,
            cache_key=key,
            error=tried.error,
            metrics=tried.metrics,
        )
        self.events.emit(
            "step_failed",
            step=name,
            attempts=tried.attempts,
            duration=round(tried.duration, 4),
            error=tried.error,
        )
        for child in self.pipeline.downstream(name):
            if child in self.outcomes:
                continue
            self.outcomes[child] = StepOutcome(child, "skipped", error=f"{name} failed")
            self.ex.lineage.record_step(
                self.id, child, "skipped", error=f"upstream {name!r} failed"
            )
            self.events.emit("step_skipped", step=child, because=name)

    def _release(self, name: str) -> None:
        """Unblock children, and drop in-memory values nobody needs anymore."""
        for child in self.consumers[name]:
            self.waiting[child].discard(name)
        with self._values_lock:
            for parent in self.pipeline.upstream(name):
                if all(c in self.outcomes for c in self.consumers[parent]):
                    self.values.pop(parent, None)

    def _value_of(self, name: str) -> Any:
        with self._values_lock:
            if name in self.values:
                return self.values[name]
        art = self.outcomes[name].artifact
        assert art is not None
        value = self.ex.store.get(art.id, art.kind)
        with self._values_lock:
            self.values[name] = value
        return value


def _call(step: Step, kwargs: dict[str, Any], ctx: StepContext) -> Any:
    run_ctx = contextvars.copy_context()
    run_ctx.run(_current.set, ctx)
    if step.timeout is None:
        return run_ctx.run(step.fn, **kwargs)

    # Python can't kill a thread, so a timed-out attempt is abandoned rather
    # than stopped; it finishes in the background and its result is dropped.
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = run_ctx.run(step.fn, **kwargs)
        except BaseException as exc:
            box["error"] = exc

    worker = threading.Thread(target=target, name=f"conveyor-{step.name}", daemon=True)
    worker.start()
    worker.join(step.timeout)
    if worker.is_alive():
        ctx.abandoned = True
        raise StepTimeout(f"{step.name} took longer than {step.timeout:g}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _trim_traceback(exc: BaseException) -> str:
    # Drop the executor's own frames; the user wants to see their step.
    frames = traceback.extract_tb(exc.__traceback__)
    here = os.path.dirname(os.path.abspath(__file__)) + os.sep
    frames = [f for f in frames if not f.filename.startswith(here)]
    cwd = os.getcwd() + os.sep
    for f in frames:
        if f.filename.startswith(cwd):
            f.filename = f.filename[len(cwd) :]
    lines = traceback.format_list(frames)
    lines += traceback.format_exception_only(type(exc), exc)
    return "".join(lines).rstrip()
