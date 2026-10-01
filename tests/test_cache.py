import importlib
import importlib.util
import sys
import textwrap

import numpy as np

from conveyor import Executor, Pipeline, step

calls: list[str] = []


@step
def numbers(n=5):
    calls.append("numbers")
    return np.arange(n, dtype=float)


@step
def total(numbers):
    calls.append("total")
    return float(numbers.sum())


@step
def parity(total):
    calls.append("parity")
    return int(total) % 2


def statuses(result):
    return {n: s.status for n, s in result.steps.items()}


def pipeline(*extra, **params):
    return Pipeline("p", [numbers, total, parity, *extra], params=params or None)


def test_second_run_is_all_cache_hits(executor):
    first = executor.run(pipeline())
    calls.clear()
    second = executor.run(pipeline())
    assert set(statuses(second).values()) == {"cached"}
    assert calls == []
    assert second.output("parity") == first.output("parity")
    assert second.steps["total"].artifact.id == first.steps["total"].artifact.id


def test_param_change_reruns_only_what_reads_it_or_depends_on_it(executor):
    executor.run(pipeline())
    calls.clear()
    result = executor.run(pipeline(), {"n": 6})
    assert statuses(result) == {
        "numbers": "succeeded",
        "total": "succeeded",
        "parity": "succeeded",
    }
    # 0..4 sums to 10 and 0..5 to 15: parity changes, so it had to run
    assert result.output("parity") == 1


def test_identical_upstream_output_keeps_downstream_cached(executor):
    executor.run(pipeline())

    @step
    def total(numbers):
        calls.append("total")
        return float(numbers.sum()) + 0.0  # different code, same answer

    calls.clear()
    result = executor.run(Pipeline("p", [numbers, total, parity]))
    assert statuses(result) == {
        "numbers": "cached",
        "total": "succeeded",
        "parity": "cached",
    }


def _load(path, name, source):
    path.write_text(textwrap.dedent(source))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_code_change_invalidates_but_formatting_does_not(tmp_path):
    ex = Executor(tmp_path / "ws")
    v1 = _load(
        tmp_path / "v1.py",
        "steps_v1",
        """
        from conveyor import step

        @step
        def double(x=2):
            return x * 2
        """,
    )
    reformatted = _load(
        tmp_path / "v1_fmt.py",
        "steps_v1_fmt",
        """
        from conveyor import step

        @step(retries=3)
        def double(x = 2):
            # same logic, new comment and spacing
            return (x * 2)
        """,
    )
    v2 = _load(
        tmp_path / "v2.py",
        "steps_v2",
        """
        from conveyor import step

        @step
        def double(x=2):
            return x * 3
        """,
    )
    assert ex.run(Pipeline("p", [v1.double])).steps["double"].status == "succeeded"
    same = ex.run(Pipeline("p", [reformatted.double]))
    assert same.steps["double"].status == "cached"
    changed = ex.run(Pipeline("p", [v2.double]))
    assert changed.steps["double"].status == "succeeded"
    assert changed.output("double") == 6


def test_version_bump_invalidates(executor):
    executor.run(pipeline())
    bumped = step(version="2")(numbers.fn)
    result = executor.run(Pipeline("p", [bumped, total, parity]))
    assert result.steps["numbers"].status == "succeeded"
    assert result.steps["total"].status == "cached"


def test_no_cache_and_missing_files_recompute(executor, tmp_path):
    first = executor.run(pipeline())
    fresh = Executor(executor.workspace, cache=False).run(pipeline())
    assert set(statuses(fresh).values()) == {"succeeded"}

    first.steps["total"].artifact.path.unlink()
    healed = executor.run(pipeline())
    assert statuses(healed) == {
        "numbers": "cached",
        "total": "succeeded",
        "parity": "cached",
    }


HELPERS = """
def _factor():
    return {factor}


def scale(x):
    return x * _factor()


def offset(x):
    return x + {offset}
"""

STEPS = """
from conveyor import step
from helpers import scale


@step
def scaled(x=3):
    return scale(x)
"""


def test_editing_a_helper_it_calls_invalidates_the_step(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    # same-second rewrites must not be served from a stale .pyc
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    ex = Executor(tmp_path / "ws")

    def load(factor, offset):
        (tmp_path / "helpers.py").write_text(
            HELPERS.format(factor=factor, offset=offset)
        )
        (tmp_path / "steps.py").write_text(STEPS)
        for name in ("helpers", "steps"):
            sys.modules.pop(name, None)
        importlib.invalidate_caches()
        return ex.run(Pipeline("p", [importlib.import_module("steps").scaled]))

    assert load(2, 1).output("scaled") == 6
    # offset isn't reachable from scaled
    assert load(2, 100).steps["scaled"].status == "cached"
    # _factor is two calls down
    changed = load(20, 100)
    assert changed.steps["scaled"].status == "succeeded"
    assert changed.output("scaled") == 60


def test_values_captured_by_a_step_factory_are_part_of_the_key(executor):
    def scaled_by(factor):
        @step(name="scaled")
        def scaled(x=3):
            return x * factor

        return scaled

    assert executor.run(Pipeline("p", [scaled_by(2)])).output("scaled") == 6
    other = executor.run(Pipeline("p", [scaled_by(5)]))
    assert other.steps["scaled"].status == "succeeded"
    assert other.output("scaled") == 15

