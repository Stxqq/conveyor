from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .data import CATEGORICAL, NUMERIC

# Heavy right tails; the model sees log1p of these.
LOG_SCALED = {"total_charges", "sessions_30d"}
# Missing for a reason (new customers, people who skip the age field), so the
# model also gets a flag rather than only the imputed median.
FLAGGED = ("age", "total_charges")


@dataclass
class Encoder:
    """Impute, clip, scale and one-hot. Every statistic comes from the
    training months only, so the test months stay unseen."""

    numeric: list[str]
    median: np.ndarray
    low: np.ndarray
    high: np.ndarray
    mean: np.ndarray
    std: np.ndarray
    levels: dict[str, list[str]]

    @classmethod
    def fit(cls, frame: dict[str, np.ndarray]) -> Encoder:
        raw = np.column_stack([_numeric(frame, c) for c in NUMERIC])
        median = np.nanmedian(raw, axis=0)
        filled = np.where(np.isnan(raw), median, raw)
        # 0.5/99.5 percentile clipping tames the x10 billing glitches without
        # touching the honest tail.
        low, high = np.percentile(filled, [0.5, 99.5], axis=0)
        clipped = np.clip(filled, low, high)
        levels = {c: sorted(np.unique(frame[c]).tolist()) for c in CATEGORICAL}
        return cls(
            numeric=list(NUMERIC),
            median=median,
            low=low,
            high=high,
            mean=clipped.mean(axis=0),
            std=clipped.std(axis=0) + 1e-9,
            levels=levels,
        )

    @property
    def names(self) -> list[str]:
        missing = [f"{c}_missing" for c in self.numeric if c in FLAGGED]
        onehot = [f"{c}={v}" for c, vs in self.levels.items() for v in vs]
        return self.numeric + missing + onehot

    def transform(self, frame: dict[str, np.ndarray]) -> np.ndarray:
        raw = np.column_stack([_numeric(frame, c) for c in self.numeric])
        is_nan = np.isnan(raw)
        filled = np.where(is_nan, self.median, raw)
        scaled = (np.clip(filled, self.low, self.high) - self.mean) / self.std
        flags = [is_nan[:, i] for i, c in enumerate(self.numeric) if c in FLAGGED]
        # Levels the encoder never saw become an all-zero block.
        onehot = [frame[c] == v for c, vs in self.levels.items() for v in vs]
        return np.column_stack([scaled, *flags, *onehot]).astype(np.float64)


def _numeric(frame: dict[str, np.ndarray], column: str) -> np.ndarray:
    values = frame[column].astype(np.float64)
    return np.log1p(values) if column in LOG_SCALED else values
