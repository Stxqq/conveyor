"""Synthetic subscription customers.

One row is a customer snapshot at the end of a month; ``churned`` says whether
they cancelled within the next 30 days. The generating process is a logistic
model with a slow upward trend over the year, so a random split would leak the
future and a time-based split is the honest one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CONTRACTS = np.array(["monthly", "annual", "two_year"])
PLANS = np.array(["basic", "standard", "premium"])
PAYMENTS = np.array(["card", "debit", "invoice", "wallet"])
REGIONS = np.array(["north", "south", "east", "west", "central"])
PLAN_PRICE = {"basic": 29.0, "standard": 54.0, "premium": 89.0}


@dataclass(frozen=True)
class Column:
    kind: str  # "int", "float" or "category"
    low: float | None = None
    high: float | None = None
    levels: tuple[str, ...] = ()
    max_missing: float = 0.0


SCHEMA = {
    "customer_id": Column("int", 0, None),
    "month": Column("int", 0, 24),
    "tenure_months": Column("int", 0, 120),
    "age": Column("float", 18, 100, max_missing=0.10),
    "contract": Column("category", levels=tuple(CONTRACTS)),
    "plan": Column("category", levels=tuple(PLANS)),
    "payment": Column("category", levels=tuple(PAYMENTS)),
    "region": Column("category", levels=tuple(REGIONS)),
    "monthly_charges": Column("float", 0, 2000),
    "total_charges": Column("float", 0, 200_000, max_missing=0.10),
    "sessions_30d": Column("int", 0, 10_000),
    "support_tickets_90d": Column("int", 0, 50),
    "late_payments_12m": Column("int", 0, 12),
}
LABEL = "churned"
NUMERIC = [
    "tenure_months",
    "age",
    "monthly_charges",
    "total_charges",
    "sessions_30d",
    "support_tickets_90d",
    "late_payments_12m",
]
CATEGORICAL = ["contract", "plan", "payment", "region"]


def simulate(
    n: int,
    seed: int,
    months: range = range(12),
    price_increase: float = 0.0,
    corrupt: float = 0.0,
) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    month = rng.choice(np.array(months), size=n)

    contract = rng.choice(CONTRACTS, size=n, p=[0.55, 0.28, 0.17])
    plan = rng.choice(PLANS, size=n, p=[0.45, 0.38, 0.17])
    # Invoice payers skew older; that correlation is what makes age informative
    # only partly on its own.
    age = np.clip(rng.normal(44, 13, size=n), 18, 92).round()
    invoice_p = np.clip(0.12 + (age - 44) * 0.004, 0.03, 0.4)
    payment = np.where(
        rng.random(n) < invoice_p,
        "invoice",
        rng.choice(np.array(["card", "debit", "wallet"]), size=n, p=[0.5, 0.3, 0.2]),
    )
    region = rng.choice(REGIONS, size=n, p=[0.24, 0.21, 0.2, 0.2, 0.15])

    max_tenure = np.where(
        contract == "two_year", 72, np.where(contract == "annual", 60, 48)
    )
    tenure = np.minimum(rng.gamma(1.6, 14, size=n), max_tenure).astype(np.int64)
    base_price = np.vectorize(PLAN_PRICE.get)(plan)
    monthly = base_price * rng.normal(1.0, 0.08, size=n) * (1 + price_increase)
    monthly = monthly + (contract == "monthly") * 6.0
    total = tenure * monthly * rng.normal(1.0, 0.04, size=n)

    engagement = rng.lognormal(mean=3.0 - 0.15 * (plan == "basic"), sigma=0.9, size=n)
    sessions = rng.poisson(engagement).astype(np.int64)
    tickets = rng.poisson(0.35 + 0.012 * np.maximum(0, 30 - sessions), size=n)
    late = rng.poisson(np.where(payment == "invoice", 0.9, 0.25), size=n)

    logit = (
        -0.8
        + np.select([contract == "monthly", contract == "annual"], [0.95, -0.35], -1.4)
        - 0.045 * np.minimum(tenure, 48)
        + 0.34 * tickets
        + 0.24 * late
        - 0.42 * np.log1p(sessions)
        + 0.018 * (monthly - 55)
        + np.select([payment == "invoice", payment == "wallet"], [0.35, 0.12], 0.0)
        - 0.012 * (age - 44)
        + 0.08 * (region == "south")
        + 0.035 * month
    )
    churned = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(np.int64)

    # Billing glitches: a handful of charges recorded in cents instead of euros.
    glitch = rng.random(n) < 0.004
    monthly = np.where(glitch, monthly * 10, monthly)
    age = np.where(rng.random(n) < 0.04, np.nan, age)
    # New customers have no invoice history yet; the field is blank, not zero.
    total = np.where((tenure == 0) | (rng.random(n) < 0.015), np.nan, total)

    frame = {
        "customer_id": 100_000 + rng.permutation(n).astype(np.int64),
        "month": month.astype(np.int64),
        "tenure_months": tenure,
        "age": age,
        "contract": contract.astype("<U10"),
        "plan": plan.astype("<U10"),
        "payment": payment.astype("<U10"),
        "region": region.astype("<U10"),
        "monthly_charges": monthly.round(2),
        "total_charges": total.round(2),
        "sessions_30d": sessions,
        "support_tickets_90d": tickets.astype(np.int64),
        "late_payments_12m": late.astype(np.int64),
        LABEL: churned,
    }
    # The export job sometimes ships a row twice.
    repeat = np.flatnonzero(rng.random(n) < 0.003)
    frame = {k: np.concatenate([v, v[repeat]]) for k, v in frame.items()}
    if corrupt > 0:
        _corrupt(frame, rng, corrupt)
    return frame


def _corrupt(
    frame: dict[str, np.ndarray], rng: np.random.Generator, rate: float
) -> None:
    """What a bad upstream export looks like: unit mixups, typos, sign flips."""
    n = len(frame["month"])
    hit = rng.random(n) < rate
    frame["age"] = np.where(
        hit & (rng.random(n) < 0.5), frame["age"] * 3.7, frame["age"]
    )
    frame["tenure_months"] = np.where(
        hit & (rng.random(n) < 0.3), -frame["tenure_months"] - 1, frame["tenure_months"]
    )
    frame["contract"] = np.where(
        hit & (rng.random(n) < 0.4) & (frame["contract"] == "monthly"),
        "montly",
        frame["contract"],
    )
    frame["total_charges"] = np.where(
        hit & (rng.random(n) < 0.6), np.nan, frame["total_charges"]
    )


def check(
    frame: dict[str, np.ndarray], schema: dict[str, Column] = SCHEMA
) -> list[str]:
    """Return one readable line per failed check; empty means the frame is fine."""
    problems = []
    n = len(next(iter(frame.values())))
    for name, col in schema.items():
        if name not in frame:
            problems.append(f"{name:<20} missing column")
            continue
        values = frame[name]
        if len(values) != n:
            problems.append(f"{name:<20} has {len(values)} rows, expected {n}")
            continue
        if col.kind == "category":
            if values.dtype.kind != "U":
                problems.append(f"{name:<20} expected strings, got {values.dtype}")
                continue
            unknown = ~np.isin(values, col.levels)
            if unknown.any():
                seen = ", ".join(repr(str(v)) for v in np.unique(values[unknown])[:3])
                problems.append(
                    f"{name:<20} {unknown.sum():>6} rows not in levels: {seen}"
                )
            continue
        if values.dtype.kind not in "iuf":
            problems.append(f"{name:<20} expected numbers, got {values.dtype}")
            continue
        missing = np.isnan(values) if values.dtype.kind == "f" else np.zeros(n, bool)
        if missing.mean() > col.max_missing:
            problems.append(
                f"{name:<20} {missing.mean():>6.1%} missing,"
                f" at most {col.max_missing:.0%} allowed"
            )
        present = values[~missing]
        if col.low is not None and (low := present < col.low).any():
            problems.append(
                f"{name:<20} {low.sum():>6} rows below {col.low:g}"
                f" (lowest {present.min():g})"
            )
        if col.high is not None and (high := present > col.high).any():
            problems.append(
                f"{name:<20} {high.sum():>6} rows above {col.high:g}"
                f" (highest {present.max():g})"
            )
    return problems
