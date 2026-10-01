from __future__ import annotations

import difflib
from collections.abc import Iterable, Mapping
from typing import Any

from .step import Step


class PipelineError(Exception):
    """The pipeline graph is invalid: duplicate names, a cycle, or a missing input."""


class Pipeline:
    """A named DAG of steps plus the parameters they read.

    Edges come from parameter names: ``def train(features, l2=1.0)`` depends on
    the step ``features`` and reads the parameter ``l2``.
    """

    def __init__(
        self,
        name: str,
        steps: Iterable[Step],
        params: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.steps: dict[str, Step] = {}
        for s in steps:
            if not isinstance(s, Step):
                raise PipelineError(f"{s!r} is not a step; did you forget @step?")
            if s.name in self.steps:
                raise PipelineError(f"two steps are named {s.name!r}")
            self.steps[s.name] = s
        self.params = self._collect_params(params or {})
        self._check_inputs()
        self.order = self._topological_order()

    def upstream(self, name: str) -> list[str]:
        return [i for i in self.steps[name].inputs if i in self.steps]

    def step_params(self, name: str) -> list[str]:
        return [i for i in self.steps[name].inputs if i not in self.steps]

    def downstream(self, name: str) -> list[str]:
        """Every step that transitively depends on ``name``, in run order."""
        hit = {name}
        for n in self.order:
            if any(u in hit for u in self.upstream(n)):
                hit.add(n)
        hit.discard(name)
        return [n for n in self.order if n in hit]

    def edges(self) -> list[tuple[str, str]]:
        return [(u, n) for n in self.order for u in self.upstream(n)]

    def resolve_params(self, overrides: Mapping[str, Any] | None = None) -> dict:
        resolved = dict(self.params)
        for key, value in (overrides or {}).items():
            if key not in resolved:
                hint = _suggest(key, resolved)
                raise PipelineError(f"unknown parameter {key!r}{hint}")
            resolved[key] = value
        return resolved

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": n,
                "inputs": self.upstream(n),
                "params": self.step_params(n),
                "retries": self.steps[n].retries,
                "timeout": self.steps[n].timeout,
                "cache": self.steps[n].cache,
            }
            for n in self.order
        ]

    def _collect_params(self, given: Mapping[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        owner: dict[str, str] = {}
        for s in self.steps.values():
            for key, default in s.defaults.items():
                if key in self.steps:
                    continue
                if key in params and params[key] != default and key not in given:
                    raise PipelineError(
                        f"parameter {key!r} defaults to {params[key]!r} in "
                        f"{owner[key]!r} but to {default!r} in {s.name!r}; "
                        "set it on the Pipeline or give the parameters different names"
                    )
                params.setdefault(key, default)
                owner.setdefault(key, s.name)
        for key, value in given.items():
            if key in self.steps:
                raise PipelineError(f"parameter {key!r} shadows a step of that name")
            params[key] = value
        return params

    def _check_inputs(self) -> None:
        problems = []
        for s in self.steps.values():
            for i in s.inputs:
                if i in self.steps or i in self.params:
                    continue
                hint = _suggest(i, self.steps)
                problems.append(
                    f"  {s.name}({i}): no step named {i!r} and no value for it{hint}"
                )
        if problems:
            raise PipelineError("missing inputs\n" + "\n".join(problems))

    def _topological_order(self) -> list[str]:
        # Kahn's algorithm, breaking ties by declaration order so runs and
        # printouts are stable.
        indegree = {n: len(self.upstream(n)) for n in self.steps}
        children: dict[str, list[str]] = {n: [] for n in self.steps}
        for n in self.steps:
            for u in self.upstream(n):
                children[u].append(n)
        position = {n: i for i, n in enumerate(self.steps)}
        frontier = sorted((n for n, d in indegree.items() if d == 0), key=position.get)
        order = []
        while frontier:
            n = frontier.pop(0)
            order.append(n)
            for c in children[n]:
                indegree[c] -= 1
                if indegree[c] == 0:
                    frontier.append(c)
                    frontier.sort(key=position.get)
        if len(order) < len(self.steps):
            raise PipelineError("cycle: " + " -> ".join(self._find_cycle()))
        return order

    def _find_cycle(self) -> list[str]:
        state: dict[str, int] = {}
        path: list[str] = []

        def visit(n: str) -> list[str] | None:
            state[n] = 1
            path.append(n)
            for u in self.upstream(n):
                if state.get(u) == 1:
                    loop = [*path[path.index(u) :], u]
                    return loop[::-1]
                if u not in state and (found := visit(u)):
                    return found
            path.pop()
            state[n] = 2
            return None

        for n in self.steps:
            if n not in state and (found := visit(n)):
                return found
        return []

    def __repr__(self) -> str:
        return f"<Pipeline {self.name} steps={len(self.steps)}>"


def _suggest(word: str, choices: Iterable[str]) -> str:
    close = difflib.get_close_matches(word, list(choices), n=1, cutoff=0.7)
    return f" (did you mean {close[0]!r}?)" if close else ""
