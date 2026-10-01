import numpy as np

from conveyor import digest
from conveyor.store import ArtifactStore


def test_round_trips_by_type(tmp_path):
    store = ArtifactStore(tmp_path)
    values = {
        "array": np.arange(6, dtype=np.float32).reshape(2, 3),
        "table": {"train": {"X": np.ones((2, 2)), "y": np.array(["a", "b"])}},
        "json": {"auc": 0.81, "bins": [1, 2, 3], "ok": True},
        "pickle": {"mixed": np.zeros(2), "n": 3},
    }
    for kind, value in values.items():
        art = store.put(value)
        assert art.kind == kind
        assert art.path.suffix == (
            ".npz"
            if kind in ("array", "table")
            else {"json": ".json", "pickle": ".pkl"}[kind]
        )
        assert digest(store.get(art.id, art.kind)) == art.id


def test_ids_are_content_hashes(tmp_path):
    store = ArtifactStore(tmp_path)
    a = store.put({"x": np.arange(3)})
    b = store.put({"x": np.arange(3)})
    assert a.id == b.id
    assert store.put({"x": np.arange(4)}).id != a.id


def test_digest_distinguishes_types_and_dtypes():
    assert digest(1) != digest(1.0) != digest(True)
    assert digest(np.zeros(3, np.float32)) != digest(np.zeros(3, np.float64))
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest([1, 2]) != digest((1, 2))
