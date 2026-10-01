import json
import threading
import time

import pytest

from conveyor import Pipeline, StepTimeout, current, log_metric, step


def test_independent_steps_really_run_at_the_same_time(executor):
    # Every branch blocks until all four are inside the barrier at once,
    # which only works if the pool runs them concurrently.
    barrier = threading.Barrier(4, timeout=5)

    @step
    def source():
        return 1

    def branch(name):
        def fn(source):
            barrier.wait()
            return threading.get_ident()

        return step(name=name)(fn)

    @step
    def join(b0, b1, b2, b3):
        return len({b0, b1, b2, b3})

    branches = [branch(f"b{i}") for i in range(4)]
    result = executor.run(Pipeline("fan", [source, *branches, join]))
    assert result.ok
    assert result.output("join") == 4


def test_downstream_of_a_failure_is_skipped_and_other_branches_finish(executor):
    @step
    def root():
        return 1

    @step
    def broken(root):
        raise RuntimeError("disk full")

    @step
    def after(broken):
        return broken

    @step
    def way_after(after):
        return after

    @step
    def sibling(root):
        return root + 1

    result = executor.run(Pipeline("p", [root, broken, after, way_after, sibling]))
    assert result.status == "failed"
    statuses = {n: s.status for n, s in result.steps.items()}
    assert statuses == {
        "root": "succeeded",
        "broken": "failed",
        "after": "skipped",
        "way_after": "skipped",
        "sibling": "succeeded",
    }
    assert "RuntimeError: disk full" in result.steps["broken"].error
    run = executor.lineage.run(result.id)
    assert run["status"] == "failed"
    assert [s["status"] for s in run["steps"]].count("skipped") == 2


def test_retries_until_success(executor):
    calls = []

    @step(retries=3, backoff=0.001)
    def flaky():
        calls.append(current().attempt)
        if len(calls) < 3:
            raise ConnectionError("try again")
        return "ok"

    result = executor.run(Pipeline("p", [flaky]))
    assert result.ok
    assert calls == [1, 2, 3]
    assert result.steps["flaky"].attempts == 3
    events = [json.loads(line) for line in result.events_path.read_text().splitlines()]
    retries = [e for e in events if e["type"] == "step_retry"]
    assert [e["attempt"] for e in retries] == [1, 2]


def test_gives_up_after_the_retry_budget(executor):
    attempts = []

    @step(retries=2, backoff=0.001)
    def down():
        attempts.append(1)
        raise ConnectionError("still down")

    result = executor.run(Pipeline("p", [down]))
    assert result.steps["down"].status == "failed"
    assert len(attempts) == 3


def test_only_listed_exceptions_are_retried(executor):
    attempts = []

    @step(retries=5, backoff=0.001, retry_on=(ConnectionError,))
    def strict():
        attempts.append(1)
        raise ValueError("bad input will stay bad")

    result = executor.run(Pipeline("p", [strict]))
    assert result.status == "failed"
    assert len(attempts) == 1


def test_an_output_that_cannot_be_stored_fails_the_step(executor):
    @step
    def unpicklable():
        return lambda x: x

    @step
    def after(unpicklable):
        return 1

    result = executor.run(Pipeline("p", [unpicklable, after]))
    assert result.steps["unpicklable"].status == "failed"
    assert "pickle" in result.steps["unpicklable"].error.lower()
    assert result.steps["after"].status == "skipped"


def test_backoff_grows_with_jitter():
    from conveyor.executor import backoff_delay

    for attempt in (1, 2, 3, 4):
        delays = [backoff_delay(0.5, attempt) for _ in range(200)]
        window = 0.5 * 2 ** (attempt - 1)
        assert window / 2 <= min(delays) and max(delays) <= window
        assert len(set(delays)) > 100
    assert backoff_delay(10, 20) <= 60


def test_timeout_fails_the_step_without_waiting_for_it(executor):
    @step(timeout=0.1)
    def slow():
        time.sleep(2)

    t0 = time.perf_counter()
    result = executor.run(Pipeline("p", [slow]))
    assert time.perf_counter() - t0 < 1.5
    assert result.steps["slow"].status == "failed"
    assert StepTimeout.__name__ in result.steps["slow"].error


def test_metrics_land_in_lineage_and_events(executor):
    @step
    def fit():
        log_metric("auc", 0.75)
        log_metric("loss", float("nan"))
        return 1

    result = executor.run(Pipeline("p", [fit]))
    assert result.steps["fit"].metrics == {"auc": 0.75, "loss": None}
    run = executor.lineage.run(result.id)
    assert run["steps"][0]["metrics"] == {"auc": 0.75, "loss": None}
    text = result.events_path.read_text()
    assert "NaN" not in text
    kinds = [json.loads(line)["type"] for line in text.splitlines()]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"
    assert kinds.count("metric") == 2


def test_current_outside_a_step():
    with pytest.raises(RuntimeError, match="not inside"):
        current()
