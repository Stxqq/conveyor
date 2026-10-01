from __future__ import annotations

import json
import os
import pickle
import re
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hashing import digest

try:
    import fcntl
except ImportError:  # Windows: only threads in one process are kept apart
    fcntl = None  # type: ignore[assignment]

_lock = threading.Lock()


@dataclass(frozen=True)
class ModelVersion:
    name: str
    version: int
    digest: str
    created_at: float
    path: Path
    card: dict[str, Any]

    @property
    def ref(self) -> str:
        return f"{self.name}:{self.version}"

    def load(self) -> Any:
        root = self.card.get("import_root")
        if root and root not in sys.path:
            # the model's class lives in the pipeline's package; make it importable
            sys.path.insert(0, root)
        with (self.path / "model.pkl").open("rb") as fh:
            return pickle.load(fh)


class ModelRegistry:
    """Versioned models on disk with a champion pointer per model name.

    ``<workspace>/registry/<name>/index.json`` lists the versions;
    each version keeps ``model.pkl`` and a ``card.json`` next to it.
    """

    def __init__(self, workspace: str | os.PathLike[str]) -> None:
        self.root = Path(workspace) / "registry"

    def names(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.parent.name for p in self.root.glob("*/index.json"))

    def versions(self, name: str) -> list[ModelVersion]:
        return [self._version(name, v) for v in self._index(name)["versions"]]

    def champion(self, name: str) -> ModelVersion | None:
        number = self._index(name)["champion"]
        return self.get(name, number) if number else None

    def get(self, name: str, version: int) -> ModelVersion:
        for v in self._index(name)["versions"]:
            if v["version"] == version:
                return self._version(name, v)
        raise LookupError(f"no model {name}:{version}")

    def resolve(self, ref: str) -> ModelVersion:
        """``churn``, ``churn:champion``, ``churn:3`` or ``churn:v3``."""
        name, _, tag = ref.partition(":")
        if tag in ("", "champion"):
            found = self.champion(name)
            if found is None:
                raise LookupError(f"{name!r} has no champion yet")
            return found
        match = re.fullmatch(r"v?(\d+)", tag)
        if not match:
            raise LookupError(f"can't read version {tag!r}; use a number or champion")
        return self.get(name, int(match.group(1)))

    def register(
        self, name: str, model: Any, card: dict[str, Any] | None = None
    ) -> ModelVersion:
        """Store a new version, or return the existing one for an identical model."""
        model_digest = digest(model)
        with self._locked(name):
            index = self._index(name)
            for v in index["versions"]:
                if v["digest"] == model_digest:
                    return self._version(name, v)
            number = max((v["version"] for v in index["versions"]), default=0) + 1
            folder = self.root / name / f"v{number}"
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / "model.pkl").open("wb") as fh:
                pickle.dump(model, fh, protocol=5)
            full_card = {
                "name": name,
                "version": number,
                "digest": model_digest,
                "created_at": time.time(),
                "class": f"{type(model).__module__}.{type(model).__qualname__}",
                "import_root": _import_root(model),
                **(card or {}),
            }
            _write_json(folder / "card.json", full_card)
            entry = {
                "version": number,
                "digest": model_digest,
                "created_at": full_card["created_at"],
            }
            index["versions"].append(entry)
            self._save(name, index)
            return self._version(name, entry)

    def promote(self, name: str, version: int, reason: str = "") -> None:
        with self._locked(name):
            index = self._index(name)
            if not any(v["version"] == version for v in index["versions"]):
                raise LookupError(f"no model {name}:{version}")
            index["champion"] = version
            index.setdefault("history", []).append(
                {"version": version, "at": time.time(), "reason": reason}
            )
            self._save(name, index)

    @contextmanager
    def _locked(self, name: str) -> Iterator[None]:
        # Two `conveyor run`s on one workspace would otherwise both read the
        # index, pick the same version number and overwrite each other.
        path = self.root / name / ".lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a") as fh:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_EX)
            yield

    def _index(self, name: str) -> dict[str, Any]:
        path = self.root / name / "index.json"
        if not path.exists():
            return {"champion": None, "versions": []}
        return json.loads(path.read_text())

    def _save(self, name: str, index: dict[str, Any]) -> None:
        _write_json(self.root / name / "index.json", index)

    def _version(self, name: str, entry: dict[str, Any]) -> ModelVersion:
        folder = self.root / name / f"v{entry['version']}"
        return ModelVersion(
            name=name,
            version=entry["version"],
            digest=entry["digest"],
            created_at=entry["created_at"],
            path=folder,
            card=json.loads((folder / "card.json").read_text()),
        )


def _import_root(obj: Any) -> str | None:
    """The sys.path entry the object's module was imported from."""
    module = sys.modules.get(type(obj).__module__)
    file = getattr(module, "__file__", None)
    if module is None or file is None or module.__name__ == "__main__":
        return None
    depth = module.__name__.count(".")
    if Path(file).name == "__init__.py":
        depth += 1
    return str(Path(file).resolve().parents[depth])


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str))
    os.replace(tmp, path)
