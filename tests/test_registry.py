from dataclasses import dataclass

import numpy as np
import pytest

from conveyor.registry import ModelRegistry


@dataclass
class Linear:
    w: np.ndarray

    def predict(self, rows):
        return [float(self.w @ [r["a"], r["b"]]) for r in rows]


def test_versions_promotion_and_refs(tmp_path):
    reg = ModelRegistry(tmp_path)
    v1 = reg.register("m", Linear(np.array([1.0, 2.0])), {"metrics": {"auc": 0.7}})
    v2 = reg.register("m", Linear(np.array([1.5, 2.0])), {"metrics": {"auc": 0.72}})
    assert (v1.version, v2.version) == (1, 2)
    assert reg.champion("m") is None
    with pytest.raises(LookupError, match="no champion"):
        reg.resolve("m")

    reg.promote("m", 2, "better")
    assert reg.resolve("m").version == 2
    assert reg.resolve("m:champion").version == 2
    assert reg.resolve("m:v1").card["metrics"] == {"auc": 0.7}
    assert reg.resolve("m:1").load().predict([{"a": 1, "b": 1}]) == [3.0]
    assert reg.names() == ["m"]
    with pytest.raises(LookupError):
        reg.resolve("m:latest")
    with pytest.raises(LookupError):
        reg.promote("m", 9)


def test_registering_the_same_model_twice_is_a_no_op(tmp_path):
    reg = ModelRegistry(tmp_path)
    a = reg.register("m", Linear(np.array([1.0, 2.0])))
    b = reg.register("m", Linear(np.array([1.0, 2.0])))
    assert a.version == b.version == 1
    assert len(reg.versions("m")) == 1
    card = a.card
    assert card["class"].endswith("Linear") and card["digest"] == a.digest
