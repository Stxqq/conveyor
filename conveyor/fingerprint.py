from __future__ import annotations

import ast
import contextlib
import hashlib
import inspect
import pickle
import sys
import sysconfig
import textwrap
import types
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np

from .hashing import digest

_DATA = (str, bytes, int, float, bool, type(None), tuple, list, dict, set, frozenset)


def code_fingerprint(fn: Callable[..., Any]) -> str:
    """Hash of a step's code and of the project code it reaches.

    The step's own source counts with decorators, comments and formatting
    stripped. Functions, classes and modules it refers to are followed as long
    as they live in your project rather than the standard library or
    site-packages, and constants they read are hashed by value. Editing a
    helper three calls deep invalidates the step; upgrading numpy doesn't.
    """
    h = hashlib.sha256()
    _Walk(h).visit(fn)
    return h.hexdigest()


class _Walk:
    def __init__(self, h: Any) -> None:
        self.h = h
        self.seen: set[int] = set()

    def visit(self, obj: Any) -> None:
        if id(obj) in self.seen:
            return
        self.seen.add(id(obj))
        if isinstance(obj, types.ModuleType):
            self._module(obj)
        elif isinstance(obj, type):
            self._class(obj)
        elif isinstance(obj, types.FunctionType):
            self._function(obj)

    def _function(self, fn: types.FunctionType) -> None:
        self.h.update(_source(fn).encode())
        self._names(fn.__code__, fn.__globals__)
        for cell in fn.__closure__ or ():
            # ValueError: a cell that hasn't been assigned yet
            with contextlib.suppress(ValueError):
                self._value(cell.cell_contents)

    def _class(self, cls: type) -> None:
        self.h.update(_source(cls).encode())
        for base in cls.__bases__:
            if _is_local(base):
                self.visit(base)
        for attr in vars(cls).values():
            fn = getattr(attr, "__func__", getattr(attr, "fget", attr))
            if isinstance(fn, types.FunctionType):
                self._names(fn.__code__, fn.__globals__)

    def _module(self, module: types.ModuleType) -> None:
        file = Path(module.__file__ or "")
        self.h.update(
            _normalised(
                file.read_text(encoding="utf-8"), strip_decorators=False
            ).encode()
        )
        # The file text covers everything defined in it; only follow what it
        # imports from elsewhere in the project.
        for name in sorted(vars(module)):
            obj = vars(module)[name]
            if _is_local(obj) and _file_of(obj) != file:
                self.visit(obj)

    def _names(self, code: types.CodeType, namespace: dict[str, Any]) -> None:
        for name in sorted(set(_global_names(code))):
            if name not in namespace:
                continue  # a builtin, or an attribute name like the `psi` in drift.psi
            self.h.update(f"\0{name}=".encode())
            self._value(namespace[name])

    def _value(self, obj: Any) -> None:
        if _is_local(obj):
            self.visit(obj)
        elif isinstance(obj, (np.ndarray, np.generic, *_DATA)) or (
            hasattr(obj, "__dataclass_fields__") and not isinstance(obj, type)
        ):
            try:
                self.h.update(digest(obj).encode())
            except (pickle.PicklingError, TypeError, AttributeError):
                # a container holding something unpicklable, like a lock
                self.h.update(type(obj).__qualname__.encode())


def _global_names(code: types.CodeType) -> Iterator[str]:
    yield from code.co_names
    for const in code.co_consts:
        if isinstance(const, types.CodeType):  # nested functions, lambdas
            yield from _global_names(const)


def _source(obj: Any) -> str:
    try:
        text = textwrap.dedent(inspect.getsource(obj))
    except (OSError, TypeError):
        # defined in a REPL or exec'd: no source on disk, fall back to bytecode
        code = getattr(obj, "__code__", None)
        if code is None:
            return obj.__qualname__
        return repr((code.co_code, code.co_consts, code.co_names, code.co_varnames))
    return _normalised(text)


def _normalised(text: str, strip_decorators: bool = True) -> str:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    node = tree.body[0] if strip_decorators and tree.body else None
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        # retry policies and other @step options don't change what it computes
        node.decorator_list = []
    return ast.unparse(tree)


def _file_of(obj: Any) -> Path | None:
    try:
        return Path(inspect.getfile(obj)).resolve()
    except (TypeError, OSError):
        return None


def _is_local(obj: Any) -> bool:
    if not isinstance(obj, (types.ModuleType, type, types.FunctionType)):
        return False
    file = _file_of(obj)
    if file is None or file.suffix != ".py":
        return False
    return not any(file.is_relative_to(root) for root in _installed_roots())


@cache
def _installed_roots() -> tuple[Path, ...]:
    paths = sysconfig.get_paths()
    roots = {Path(paths[k]).resolve() for k in ("stdlib", "platstdlib", "purelib")}
    roots.add(Path(paths["platlib"]).resolve())
    roots.add(Path(__file__).resolve().parent)  # conveyor itself
    roots.update(Path(p).resolve() for p in sys.path if "site-packages" in p)
    return tuple(sorted(roots))
