from __future__ import annotations

import numpy as np


def _average_ranks(x: np.ndarray) -> np.ndarray:
    """1-based ranks where tied values share the mean of their positions."""
    order = np.argsort(x, kind="mergesort")
    sorted_x = x[order]
    starts = np.flatnonzero(np.r_[True, sorted_x[1:] != sorted_x[:-1]])
    ends = np.r_[starts[1:], len(x)]
    ranks = np.empty(len(x))
    ranks[order] = np.repeat((starts + ends + 1) / 2, ends - starts)
    return ranks


def roc_auc(y: np.ndarray, score: np.ndarray) -> float:
    """Probability that a random positive outscores a random negative
    (Mann-Whitney U), with ties counted as half."""
    y = y.astype(bool)
    pos, neg = y.sum(), (~y).sum()
    if pos == 0 or neg == 0:
        return float("nan")
    rank_sum = _average_ranks(score)[y].sum()
    return float((rank_sum - pos * (pos + 1) / 2) / (pos * neg))


def average_precision(y: np.ndarray, score: np.ndarray) -> float:
    """Area under the precision-recall curve as a step function, which is
    what sklearn calls average_precision_score. Tied scores form one step."""
    order = np.argsort(-score, kind="mergesort")
    y, score = y[order].astype(float), score[order]
    last_of_tie = np.r_[score[1:] != score[:-1], True]
    tp = np.cumsum(y)[last_of_tie]
    seen = np.flatnonzero(last_of_tie) + 1
    if tp[-1] == 0:
        return float("nan")
    precision = tp / seen
    recall_gain = np.diff(np.r_[0, tp]) / tp[-1]
    return float(np.sum(precision * recall_gain))


def log_loss(y: np.ndarray, p: np.ndarray, eps: float = 1e-12) -> float:
    p = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _bins(p: np.ndarray, bins: int) -> tuple[np.ndarray, np.ndarray]:
    edges = np.linspace(0, 1, bins + 1)
    return edges, np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)


def calibration(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    """Reliability table over equal-width probability bins, rounded for display."""
    edges, which = _bins(p, bins)
    table = []
    for b in range(bins):
        mask = which == b
        if not mask.any():
            continue
        table.append(
            {
                "low": round(float(edges[b]), 2),
                "high": round(float(edges[b + 1]), 2),
                "count": int(mask.sum()),
                "predicted": round(float(p[mask].mean()), 4),
                "observed": round(float(y[mask].mean()), 4),
            }
        )
    return table


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """Count-weighted gap between mean prediction and churn rate per bin."""
    _, which = _bins(p, bins)
    gap = sum(
        mask.sum() * abs(p[mask].mean() - y[mask].mean())
        for mask in (which == b for b in range(bins))
        if mask.any()
    )
    return float(gap / len(y))


def lift_at(y: np.ndarray, score: np.ndarray, fraction: float = 0.1) -> float:
    """Churn rate in the top ``fraction`` by score over the overall rate."""
    k = max(1, round(len(y) * fraction))
    top = np.argsort(-score, kind="mergesort")[:k]
    return float(y[top].mean() / y.mean())
