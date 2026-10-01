from __future__ import annotations

import importlib
import sys
from pathlib import Path

from .dag import Pipeline


def load_pipeline(path: str | Path) -> Pipeline:
    """Import a pipeline file and return the Pipeline it defines.

    If the file sits inside a package it's imported under its dotted name, the
    way pytest does it, so classes defined next to it pickle with a stable
    module path and the model registry can load them later.
    """
    file = Path(path).resolve()
    if not file.is_file():
        raise FileNotFoundError(f"no pipeline file at {path}")
    root, parts = file.parent, [file.stem]
    while (root / "__init__.py").exists():
        parts.insert(0, root.name)
        root = root.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    module = importlib.import_module(".".join(parts))

    named = getattr(module, "pipeline", None)
    if isinstance(named, Pipeline):
        return named
    found = [v for v in vars(module).values() if isinstance(v, Pipeline)]
    if len(found) == 1:
        return found[0]
    if not found:
        raise LookupError(f"{path} doesn't define a Pipeline")
    raise LookupError(f"{path} defines several pipelines; name one of them `pipeline`")
