from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .data import CATEGORICAL, NUMERIC
from .features import Encoder


@dataclass
class Fit:
    weights: np.ndarray
    bias: float
    iterations: int
    loss: float


def fit_logistic(
    X: np.ndarray, y: np.ndarray, l2: float, max_iter: int = 50, tol: float = 1e-8
) -> Fit:
    """L2-regularised logistic regression by Newton's method.

    With a few dozen features the Hessian is tiny, so Newton converges in a
    handful of exact steps where gradient descent would need a learning rate
    and hundreds of iterations. The intercept is not penalised.
    """
    n, d = X.shape
    A = np.column_stack([np.ones(n), X])
    w = np.zeros(d + 1)
    # start the intercept at the base rate's log-odds; Newton then needs a step
    # or two less
    w[0] = np.log(y.mean() / (1 - y.mean()))
    penalty = np.full(d + 1, l2)
    penalty[0] = 0.0
    loss = np.inf
    it = 0
    while it < max_iter:
        it += 1
        z = A @ w
        p = _sigmoid(z)
        grad = A.T @ (p - y) / n + penalty * w / n
        hess = (A.T * (p * (1 - p))) @ A / n + np.diag(penalty) / n
        step = np.linalg.solve(hess, grad)
        w -= step
        loss = _loss(A @ w, y) + 0.5 * l2 * (w[1:] @ w[1:]) / n
        if np.abs(step).max() < tol:
            break
    return Fit(weights=w[1:], bias=float(w[0]), iterations=it, loss=float(loss))


@dataclass
class ChurnModel:
    """What gets registered and served: the encoder and the weights together,
    so the model takes raw customer records."""

    encoder: Encoder
    weights: np.ndarray
    bias: float
    l2: float

    def predict_proba(self, frame: dict[str, np.ndarray]) -> np.ndarray:
        return _sigmoid(self.encoder.transform(frame) @ self.weights + self.bias)

    def predict(self, rows: list[dict]) -> list[float]:
        return self.predict_proba(records_to_frame(rows)).tolist()

    def top_drivers(self, k: int = 6) -> list[dict]:
        order = np.argsort(-np.abs(self.weights))[:k]
        names = self.encoder.names
        return [
            {"feature": names[i], "weight": round(float(self.weights[i]), 4)}
            for i in order
        ]


def records_to_frame(rows: list[dict]) -> dict[str, np.ndarray]:
    if not rows:
        raise ValueError("no rows to score")
    frame: dict[str, np.ndarray] = {}
    for column in NUMERIC:
        values = [r.get(column) for r in rows]
        frame[column] = np.array([np.nan if v is None else v for v in values], float)
    for column in CATEGORICAL:
        frame[column] = np.array([str(r.get(column, "")) for r in rows])
    return frame


def _sigmoid(z: np.ndarray) -> np.ndarray:
    # the tanh form doesn't overflow for large |z| the way 1 / (1 + exp(-z)) does
    return 0.5 * (1 + np.tanh(0.5 * z))


def _loss(z: np.ndarray, y: np.ndarray) -> float:
    # log(1 + e^z) - y z, written to stay finite for large |z|
    return float(np.mean(np.logaddexp(0, z) - y * z))
