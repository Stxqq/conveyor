import json
import shutil
import threading
import urllib.request
from types import SimpleNamespace

import numpy as np
import pytest

from churn.data import simulate
from churn.model import fit_logistic
from churn.pipeline import pipeline
from conveyor.registry import ModelRegistry
from conveyor.serve import make_server


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    from conveyor import Executor

    ex = Executor(tmp_path_factory.mktemp("churn") / "ws")
    cold = ex.run(pipeline)
    warm = ex.run(pipeline)
    stricter = ex.run(pipeline, {"l2": 1000.0})
    broken = ex.run(pipeline, {"corrupt": 0.03})
    return SimpleNamespace(
        ex=ex, cold=cold, warm=warm, stricter=stricter, broken=broken
    )


def test_cold_run_trains_a_useful_model(runs):
    assert runs.cold.ok and runs.cold.count("succeeded") == len(pipeline.order)
    report = runs.cold.output("evaluate")
    assert 0.75 < report["roc_auc"] < 0.9
    assert report["pr_auc"] > 2 * report["base_rate"]
    assert report["ece"] < 0.05
    assert sum(b["count"] for b in report["calibration"]) == report["test_rows"]
    assert report["train_months"] == [0, 9] and report["test_months"] == [10, 11]


def test_rerun_only_asks_the_registry_again(runs):
    ran = [n for n, s in runs.warm.steps.items() if s.status == "succeeded"]
    assert ran == ["gate", "register"]
    assert runs.warm.output("gate")["reason"] == "already the champion"
    assert runs.warm.steps["score"].status == "cached"
    assert runs.warm.output("monitor") == runs.cold.output("monitor")


def test_cache_never_replays_a_stale_champion(tmp_path):
    from conveyor import Executor

    ex = Executor(tmp_path / "ws")
    weak = ex.run(pipeline, {"l2": 1000.0})
    strong = ex.run(pipeline)
    assert strong.output("register")["champion"] == 2
    # the weak model's run again: every input is cached, but the registry moved
    again = ex.run(pipeline, {"l2": 1000.0})
    assert again.output("register") == {"model": "churn", "version": 1, "champion": 2}
    assert again.output("score")["score"].mean() == pytest.approx(
        strong.output("score")["score"].mean()
    )
    assert again.output("score")["score"].mean() != pytest.approx(
        weak.output("score")["score"].mean()
    )

    shutil.rmtree(ex.workspace / "registry")
    fresh = ex.run(pipeline)
    assert fresh.output("gate")["reason"] == "no champion yet"
    assert ModelRegistry(ex.workspace).resolve("churn").version == 1


def test_gate_keeps_the_champion_when_the_challenger_is_worse(runs):
    decision = runs.stricter.output("gate")
    assert decision["promote"] is False
    assert decision["gain"] < 0
    reg = runs.stricter.output("register")
    assert reg == {"model": "churn", "version": 2, "champion": 1}
    # score re-ran against the unchanged champion, produced the same artifact,
    # so monitor was a cache hit
    assert runs.stricter.steps["score"].status == "succeeded"
    assert runs.stricter.steps["monitor"].status == "cached"


def test_bad_data_stops_at_validation(runs):
    assert runs.broken.status == "failed"
    assert runs.broken.steps["validate"].status == "failed"
    assert "rows below 0" in runs.broken.steps["validate"].error
    skipped = [n for n, s in runs.broken.steps.items() if s.status == "skipped"]
    assert "train" in skipped and "register" in skipped
    assert runs.broken.steps["new_month"].status == "cached"


def test_monitor_catches_the_price_change(runs):
    report = runs.cold.output("monitor")
    assert report["flagged"][0] == "monthly_charges"
    assert report["features"]["monthly_charges"]["status"] == "drift"
    assert report["features"]["region"]["status"] == "stable"


def test_serving_the_champion(runs):
    version = ModelRegistry(runs.ex.workspace).resolve("churn")
    server = make_server(version, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rows = [
            {
                "tenure_months": 2,
                "age": 31,
                "contract": "monthly",
                "plan": "premium",
                "payment": "invoice",
                "region": "south",
                "monthly_charges": 95.0,
                "total_charges": 190.0,
                "sessions_30d": 1,
                "support_tickets_90d": 4,
                "late_payments_12m": 2,
            },
            {
                "tenure_months": 60,
                "age": None,
                "contract": "two_year",
                "plan": "basic",
                "payment": "card",
                "region": "north",
                "monthly_charges": 29.0,
                "total_charges": 1740.0,
                "sessions_30d": 80,
                "support_tickets_90d": 0,
                "late_payments_12m": 0,
            },
        ]
        url = f"http://127.0.0.1:{server.server_address[1]}"
        req = urllib.request.Request(
            url + "/predict",
            data=json.dumps({"rows": rows}).encode(),
            headers={"Content-Type": "application/json"},
        )
        body = json.load(urllib.request.urlopen(req))
        assert body["model"] == "churn:1"
        risky, safe = body["predictions"]
        assert risky > 0.5 > 0.05 > safe

        bad = urllib.request.Request(url + "/predict", data=b'{"rows": 3}')
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(bad)
        assert err.value.code == 400
        card = json.load(urllib.request.urlopen(url + "/card"))
        assert card["metrics"]["roc_auc"] == pytest.approx(
            runs.cold.output("evaluate")["roc_auc"], abs=1e-5
        )
    finally:
        server.shutdown()
        server.server_close()


def test_newton_recovers_known_coefficients():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(20_000, 3))
    true_w = np.array([1.5, -2.0, 0.5])
    y = (rng.random(20_000) < 1 / (1 + np.exp(-(X @ true_w - 0.7)))).astype(float)
    fit = fit_logistic(X, y, l2=0.0)
    assert np.allclose(fit.weights, true_w, atol=0.08)
    assert fit.bias == pytest.approx(-0.7, abs=0.06)
    assert fit.iterations < 12
    shrunk = fit_logistic(X, y, l2=5000.0)
    assert np.abs(shrunk.weights).sum() < np.abs(fit.weights).sum()


def test_simulation_is_seeded():
    a, b = simulate(1000, 1), simulate(1000, 1)
    assert all(np.array_equal(a[k], b[k], equal_nan=a[k].dtype.kind == "f") for k in a)
    assert not np.array_equal(simulate(1000, 2)["age"], a["age"], equal_nan=True)
