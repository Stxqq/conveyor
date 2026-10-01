from __future__ import annotations

import json
import os
import pickle
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .hashing import digest


@dataclass(frozen=True)
class Artifact:
    id: str
    kind: str
    size: int
    path: Path


def _plain_array(value: Any) -> bool:
    return isinstance(value, np.ndarray) and value.dtype != object


def _is_table(value: Any) -> bool:
    """A dict of arrays, or of such dicts (``{"train": {...}, "test": {...}}``)."""
    return (
        isinstance(value, dict)
        and bool(value)
        and all(
            isinstance(k, str) and "/" not in k and (_plain_array(v) or _is_table(v))
            for k, v in value.items()
        )
    )


def _flatten(table: dict[str, Any], prefix: str = "") -> dict[str, np.ndarray]:
    flat: dict[str, np.ndarray] = {}
    for key, value in table.items():
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{prefix}{key}/"))
        else:
            flat[prefix + key] = value
    return flat


def _unflatten(flat: dict[str, np.ndarray]) -> dict[str, Any]:
    table: dict[str, Any] = {}
    for key, value in flat.items():
        *parents, leaf = key.split("/")
        node = table
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = value
    return table


def _is_json(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, list):
        return all(_is_json(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json(v) for k, v in value.items())
    return False


def _kind_of(value: Any) -> str:
    if _plain_array(value):
        return "array"
    if _is_table(value):
        return "table"
    if _is_json(value):
        return "json"
    return "pickle"


_EXT = {"array": ".npz", "table": ".npz", "json": ".json", "pickle": ".pkl"}


class ArtifactStore:
    """Content-addressed files under ``<root>/artifacts/ab/abcdef….<ext>``.

    Arrays and (nested) dicts of arrays go to npz, JSON-safe values to json, anything
    else is pickled.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root) / "artifacts"

    def path_for(self, artifact_id: str, kind: str) -> Path:
        return self.root / artifact_id[:2] / f"{artifact_id}{_EXT[kind]}"

    def exists(self, artifact_id: str, kind: str) -> bool:
        return self.path_for(artifact_id, kind).exists()

    def put(self, value: Any) -> Artifact:
        kind = _kind_of(value)
        artifact_id = digest(value)
        path = self.path_for(artifact_id, kind)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    _dump(value, kind, fh)
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        return Artifact(artifact_id, kind, path.stat().st_size, path)

    def get(self, artifact_id: str, kind: str) -> Any:
        path = self.path_for(artifact_id, kind)
        if kind == "json":
            return json.loads(path.read_text())
        if kind == "pickle":
            with path.open("rb") as fh:
                return pickle.load(fh)
        with np.load(path, allow_pickle=False) as npz:
            if kind == "array":
                return npz["array"]
            return _unflatten({key: npz[key] for key in npz.files})


def _dump(value: Any, kind: str, fh: Any) -> None:
    if kind == "array":
        np.savez_compressed(fh, array=value)
    elif kind == "table":
        np.savez_compressed(fh, **_flatten(value))
    elif kind == "json":
        fh.write(json.dumps(value, indent=1).encode())
    else:
        pickle.dump(value, fh, protocol=5)
