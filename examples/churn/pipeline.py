"""Monthly churn model: data checks, training, a champion/challenger gate,
batch scoring of the newest month and drift monitoring.

    conveyor run examples/churn/pipeline.py
    conveyor run examples/churn/pipeline.py -p l2=10
    conveyor run examples/churn/pipeline.py -p corrupt=0.03   # fails validation
"""

from __future__ import annotations

import random

import numpy as np

from conveyor import Pipeline, current, digest, log, log_metric, step

from . import drift, scoring
from .data import CATEGORICAL, LABEL, NUMERIC, SCHEMA, check, simulate
from .features import Encoder
from .model import ChurnModel, fit_logistic

MODEL = "churn"


class ValidationError(Exception):
    pass


@step
def customers(seed: int = 7, n_customers: int = 24_000, corrupt: float = 0.0):
    return simulate(n_customers, seed, corrupt=corrupt)


@step
def validate(customers):
    problems = check(customers)
    if problems:
        raise ValidationError(
            f"{len(problems)} checks failed on {len(customers['month'])} rows\n"
            + "\n".join(problems)
        )
    key = customers["customer_id"] * 100 + customers["month"]
    _, first = np.unique(key, return_index=True)
    keep = np.sort(first)
    log_metric("rows", len(keep))
    log_metric("duplicates_dropped", len(key) - len(keep))
    return {k: v[keep] for k, v in customers.items()}


@step
def split(validate, test_months: int = 2):
    # Train on the past, test on the most recent months, like it will be used.
    cutoff = validate["month"].max() - test_months + 1
    is_train = validate["month"] < cutoff
    train = {k: v[is_train] for k, v in validate.items()}
    test = {k: v[~is_train] for k, v in validate.items()}
    log_metric("train_rows", len(train[LABEL]))
    log_metric("test_rows", len(test[LABEL]))
    log_metric("train_churn_rate", train[LABEL].mean())
    log_metric("test_churn_rate", test[LABEL].mean())
    return {"train": train, "test": test}


@step
def encoder(split):
    return Encoder.fit(split["train"])


@step
def features(split, encoder):
    return {
        part: {"X": encoder.transform(rows), "y": rows[LABEL].astype(np.float64)}
        for part, rows in split.items()
    }


@step
def baseline(split):
    """Reference distributions from the training months, for drift checks."""
    train = split["train"]
    profile = {}
    for column in NUMERIC:
        edges = drift.numeric_edges(train[column].astype(float))
        profile[column] = {
            "kind": "numeric",
            "edges": edges,
            "shares": drift.numeric_shares(train[column], edges),
        }
    for column in CATEGORICAL:
        levels = list(SCHEMA[column].levels)
        profile[column] = {
            "kind": "category",
            "levels": levels,
            "shares": drift.category_shares(train[column], levels),
        }
    return profile


@step
def train(features, encoder, l2: float = 1.0):
    fit = fit_logistic(features["train"]["X"], features["train"]["y"], l2=l2)
    log_metric("newton_iterations", fit.iterations)
    log_metric("train_loss", fit.loss)
    return ChurnModel(encoder=encoder, weights=fit.weights, bias=fit.bias, l2=l2)


@step
def evaluate(train, split):
    test = split["test"]
    y = test[LABEL].astype(float)
    p = train.predict_proba(test)
    table = scoring.calibration(y, p)
    report = {
        "roc_auc": scoring.roc_auc(y, p),
        "pr_auc": scoring.average_precision(y, p),
        "log_loss": scoring.log_loss(y, p),
        "brier": float(np.mean((p - y) ** 2)),
        "ece": scoring.expected_calibration_error(table),
        "lift_top_decile": scoring.lift_at(y, p, 0.1),
        "base_rate": float(y.mean()),
    }
    for name, value in report.items():
        log_metric(name, value)
    report["calibration"] = table
    report["drivers"] = train.top_drivers()
    report["test_rows"] = len(y)
    report["train_months"] = _months(split["train"])
    report["test_months"] = _months(test)
    return report


@step
def gate(train, evaluate, split, min_auc_gain: float = 0.002):
    """Champion/challenger: both models score the same test months; the
    challenger wins only by a margin, so noise doesn't churn the champion."""
    challenger_auc = evaluate["roc_auc"]
    champion = current().registry.champion(MODEL)
    decision = {"challenger_auc": challenger_auc, "min_gain": min_auc_gain}
    if champion is None:
        decision.update(promote=True, reason="no champion yet")
    elif champion.digest == digest(train):
        decision.update(
            promote=False, champion=champion.version, reason="already the champion"
        )
    else:
        test = split["test"]
        champion_auc = scoring.roc_auc(test[LABEL], champion.load().predict_proba(test))
        gain = challenger_auc - champion_auc
        decision.update(
            champion=champion.version,
            champion_auc=champion_auc,
            gain=gain,
            promote=gain >= min_auc_gain,
            reason=(
                f"AUC {'+' if gain >= 0 else ''}{gain:.4f} vs v{champion.version}, "
                f"{'meets' if gain >= min_auc_gain else 'below'} the {min_auc_gain} bar"
            ),
        )
        log_metric("auc_gain", gain)
    log(decision["reason"])
    return decision


@step
def register(train, evaluate, gate):
    registry = current().registry
    card = {
        "description": "Probability that a customer cancels within 30 days.",
        "intended_use": "Ranking customers for retention offers; not for pricing.",
        "algorithm": "L2 logistic regression, Newton's method",
        "params": {"l2": train.l2},
        "features": train.encoder.names,
        "train_months": evaluate["train_months"],
        "test_months": evaluate["test_months"],
        "metrics": {
            k: round(evaluate[k], 5)
            for k in (
                "roc_auc",
                "pr_auc",
                "log_loss",
                "brier",
                "ece",
                "lift_top_decile",
            )
        },
        "calibration": evaluate["calibration"],
        "drivers": evaluate["drivers"],
        "gate": gate,
        "run_id": current().run_id,
        "caveats": [
            "Trained on synthetic data.",
            "Scores drift with pricing changes; watch the monitor step.",
        ],
    }
    version = registry.register(MODEL, train, card)
    if gate["promote"]:
        registry.promote(MODEL, version.version, gate["reason"])
    champion = registry.champion(MODEL)
    assert champion is not None
    log(f"registered {version.ref}, champion is v{champion.version}")
    return {
        "model": MODEL,
        "version": version.version,
        "champion": champion.version,
        "promoted": bool(gate["promote"]),
    }


@step(retries=3, backoff=0.25)
def new_month(
    seed: int = 7,
    n_customers: int = 24_000,
    price_increase: float = 0.12,
    flaky: float = 0.0,
):
    """The month we need scores for. ``flaky`` makes the warehouse read fail
    on some attempts, to watch the retry policy work."""
    attempt = current().attempt
    if random.Random(f"{seed}:{attempt}").random() < flaky:
        raise ConnectionError(f"warehouse read timed out (attempt {attempt})")
    frame = simulate(
        n_customers // 12, seed + 1, months=range(12, 13), price_increase=price_increase
    )
    del frame[LABEL]
    return frame


@step
def score(register, new_month):
    model = current().registry.get(MODEL, register["champion"]).load()
    p = model.predict_proba(new_month)
    log_metric("scored", len(p))
    log_metric("mean_score", p.mean())
    log_metric("high_risk_share", np.mean(p >= 0.5))
    return {"customer_id": new_month["customer_id"], "score": p}


@step
def monitor(baseline, new_month, score):
    features = {}
    for column, ref in baseline.items():
        values = new_month[column]
        if ref["kind"] == "numeric":
            shares = drift.numeric_shares(values.astype(float), ref["edges"])
        else:
            shares = drift.category_shares(values, ref["levels"])
        value = drift.psi(ref["shares"], shares)
        features[column] = {"psi": round(value, 5), "status": drift.status(value)}
    flagged = sorted(
        (c for c, f in features.items() if f["status"] != "stable"),
        key=lambda c: -features[c]["psi"],
    )
    log_metric("psi_max", max(f["psi"] for f in features.values()))
    log_metric(
        "features_drifting", sum(f["status"] == "drift" for f in features.values())
    )
    for column in flagged:
        found = features[column]
        log(f"{column}: PSI {found['psi']:.3f} ({found['status']})")
    return {
        "features": features,
        "flagged": flagged,
        "thresholds": {"watch": drift.WATCH, "drift": drift.DRIFT},
        "scores": {
            "count": len(score["score"]),
            "mean": float(score["score"].mean()),
            "p90": float(np.quantile(score["score"], 0.9)),
        },
    }


def _months(frame) -> list[int]:
    return [int(frame["month"].min()), int(frame["month"].max())]


pipeline = Pipeline(
    "churn",
    [
        customers,
        validate,
        split,
        encoder,
        features,
        baseline,
        train,
        evaluate,
        gate,
        register,
        new_month,
        score,
        monitor,
    ],
)
