from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from .fingerprint import code_fingerprint

_EMPTY = inspect.Parameter.empty


@dataclass(eq=False)
class Step:
    """A pipeline node. Its parameter names are the names of the steps or
    pipeline parameters it consumes."""

    fn: Callable[..., Any]
    name: str
    inputs: tuple[str, ...]
    defaults: dict[str, Any] = field(default_factory=dict)
    retries: int = 0
    backoff: float = 0.5
    timeout: float | None = None
    retry_on: tuple[type[BaseException], ...] = (Exception,)
    version: str = ""

    @cached_property
    def fingerprint(self) -> str:
        # Worked out on first use rather than at decoration time, so helpers
        # defined further down the module are already there to be followed.
        return code_fingerprint(self.fn)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.fn(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<step {self.name}({', '.join(self.inputs)})>"


def step(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    retries: int = 0,
    backoff: float = 0.5,
    timeout: float | None = None,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    version: str = "",
) -> Any:
    """Turn a function into a pipeline step.

    Use bare (``@step``) or with options (``@step(retries=3, timeout=30)``).
    The cache follows the step's code into your own modules; bump ``version``
    when something it can't see changes, like a file the step reads.
    """

    def wrap(f: Callable[..., Any]) -> Step:
        sig = inspect.signature(f)
        for p in sig.parameters.values():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                raise TypeError(
                    f"step {f.__name__!r}: *args/**kwargs can't be wired by name"
                )
        return Step(
            fn=f,
            name=name or f.__name__,
            inputs=tuple(sig.parameters),
            defaults={
                k: p.default
                for k, p in sig.parameters.items()
                if p.default is not _EMPTY
            },
            retries=retries,
            backoff=backoff,
            timeout=timeout,
            retry_on=retry_on,
            version=version,
        )

    return wrap(fn) if fn is not None else wrap
