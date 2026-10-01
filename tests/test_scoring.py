import itertools

import numpy as np
import pytest

from churn import drift, scoring


def pairwise_auc(y, s):
    pos = s[y == 1]
    neg = s[y == 0]
    wins = sum(
        1.0 if p > n else 0.5 if p == n else 0.0 for p, n in itertools.product(pos, neg)
    )
    return wins / (len(pos) * len(neg))


@pytest.mark.parametrize("seed", range(5))
def test_auc_matches_brute_force_pairs(seed):
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, 300)
    # rounding forces plenty of ties, the case rank formulas usually get wrong
    s = np.round(rng.normal(y * 0.8, 1.0), 1)
    assert scoring.roc_auc(y, s) == pytest.approx(pairwise_auc(y, s), abs=1e-12)


def test_auc_edges():
    y = np.array([0, 0, 1, 1])
    assert scoring.roc_auc(y, np.array([0.1, 0.2, 0.3, 0.4])) == 1.0
    assert scoring.roc_auc(y, np.array([0.4, 0.3, 0.2, 0.1])) == 0.0
    assert scoring.roc_auc(y, np.full(4, 0.5)) == 0.5
    assert np.isnan(scoring.roc_auc(np.ones(3), np.arange(3.0)))


def brute_average_precision(y, s):
    total = 0.0
    prev_recall = 0.0
    for t in sorted(set(s), reverse=True):
        picked = s >= t
        tp = (y[picked] == 1).sum()
        recall = tp / y.sum()
        total += (recall - prev_recall) * tp / picked.sum()
        prev_recall = recall
    return total


@pytest.mark.parametrize("seed", range(5))
def test_average_precision_matches_threshold_sweep(seed):
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, 200)
    s = np.round(rng.normal(y * 0.5, 1.0), 1)
    assert scoring.average_precision(y, s) == pytest.approx(
        brute_average_precision(y, s)
    )


def test_log_loss_and_calibration():
    y = np.array([0, 1, 1, 0])
    p = np.array([0.1, 0.9, 0.8, 0.3])
    expected = -np.mean(np.log([0.9, 0.9, 0.8, 0.7]))
    assert scoring.log_loss(y, p) == pytest.approx(expected)
    table = scoring.calibration(y, p, bins=2)
    assert [(r["count"], r["observed"]) for r in table] == [(2, 0.0), (2, 1.0)]
    assert scoring.expected_calibration_error(y, p, bins=2) == pytest.approx(0.175)


def test_lift_at_top_decile():
    y = np.array([1] + [0] * 19)  # one churner, ranked first
    s = -np.arange(20.0)
    assert scoring.lift_at(y, s, 0.1) == pytest.approx(0.5 / 0.05)


def test_psi_known_values():
    assert drift.psi([0.5, 0.5], [0.5, 0.5]) == 0.0
    # (0.6-0.4)ln(0.6/0.4) + (0.4-0.6)ln(0.4/0.6) = 0.4 ln 1.5
    assert drift.psi([0.4, 0.6], [0.6, 0.4]) == pytest.approx(0.4 * np.log(1.5))
    assert drift.psi([1.0, 0.0], [1.0, 0.0]) == 0.0
    assert drift.status(0.05) == "stable"
    assert drift.status(0.1) == "watch"
    assert drift.status(0.3) == "drift"


def test_psi_flags_a_shift_but_not_resampling():
    rng = np.random.default_rng(0)
    reference = rng.normal(50, 10, 20_000)
    edges = drift.numeric_edges(reference)
    ref_shares = drift.numeric_shares(reference, edges)
    assert len(edges) == 9 and sum(ref_shares) == pytest.approx(1.0)
    same = drift.numeric_shares(rng.normal(50, 10, 5_000), edges)
    moved = drift.numeric_shares(rng.normal(56, 10, 5_000), edges)
    assert drift.psi(ref_shares, same) < 0.02
    assert drift.psi(ref_shares, moved) > drift.DRIFT


def test_psi_shares_track_missing_and_unseen_levels():
    shares = drift.numeric_shares(np.array([1.0, 2.0, np.nan, 3.0]), [1.5, 2.5])
    assert shares == [0.25, 0.25, 0.25, 0.25]
    cats = drift.category_shares(np.array(["a", "b", "zz", "a"]), ["a", "b"])
    assert cats == [0.5, 0.25, 0.25]
