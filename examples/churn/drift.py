"""Population stability index.

PSI = sum((a - e) * ln(a / e)) over bins, where e and a are the share of the
reference and the current data in each bin. The usual reading: below 0.1 is
stable, 0.1 to 0.25 is worth a look, above 0.25 the population has moved.
"""

from __future__ import annotations

import numpy as np

WATCH = 0.1
DRIFT = 0.25
# Empty bins would put log(0) in the sum; this floor is the common fix.
FLOOR = 1e-4


def psi(expected: np.ndarray, actual: np.ndarray) -> float:
    e = np.clip(np.asarray(expected, float), FLOOR, None)
    a = np.clip(np.asarray(actual, float), FLOOR, None)
    return float(np.sum((a - e) * np.log(a / e)))


def numeric_edges(values: np.ndarray, bins: int = 10) -> list[float]:
    """Inner quantile edges of the reference, deduplicated for discrete data."""
    present = values[~np.isnan(values)]
    inner = np.quantile(present, np.linspace(0, 1, bins + 1)[1:-1])
    return np.unique(inner).tolist()


def numeric_shares(values: np.ndarray, edges: list[float]) -> list[float]:
    """Share per bin, with missing values as their own last bin."""
    values = np.asarray(values, float)
    missing = np.isnan(values)
    counts = np.bincount(
        np.searchsorted(edges, values[~missing], side="right"), minlength=len(edges) + 1
    )
    shares = np.r_[counts, missing.sum()] / len(values)
    return shares.tolist()


def category_shares(values: np.ndarray, levels: list[str]) -> list[float]:
    """Share per known level, plus one bucket for anything unseen."""
    known = np.array([np.mean(values == level) for level in levels])
    return [*known.tolist(), float(1 - known.sum())]


def status(value: float) -> str:
    if value >= DRIFT:
        return "drift"
    if value >= WATCH:
        return "watch"
    return "stable"
