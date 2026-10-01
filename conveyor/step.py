from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

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
    fingerprint: str = ""

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
    Bump ``version`` to invalidate the cache when a helper the step calls changes.
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
            fingerprint=source_fingerprint(f),
        )

    return wrap(fn) if fn is not None else wrap


def source_fingerprint(fn: Callable[..., Any]) -> str:
    """Hash of the function's code with decorators, comments and formatting
    stripped, so reformatting a step or changing its retry policy keeps the cache."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        node = tree.body[0]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
        text = ast.unparse(node)
    except (OSError, TypeError, SyntaxError, IndexError):
        # defined in a REPL or exec'd: no source on disk, fall back to bytecode
        code = fn.__code__
        text = repr((code.co_code, code.co_consts, code.co_names, code.co_varnames))
    return hashlib.sha256(text.encode()).hexdigest()
