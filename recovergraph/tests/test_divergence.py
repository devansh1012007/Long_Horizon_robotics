"""Divergence-statistic tests (spec 6.2).

Written BEFORE the implementation, per Section 17 step 5: "write rg_core unit
tests for the divergence statistic and the Bayes update before implementing them".
"""
import numpy as np
import pytest
from scipy import stats as sps

from rg_core.divergence import (
    chi2_ppf,
    chi2_sf,
    chi2_threshold,
    check_divergence,
    mahalanobis_D,
)


class TestMahalanobis:
    def test_zero_residual(self):
        m = np.zeros(4)
        c = np.full(4, 1e-3)
        assert mahalanobis_D(m - m, c + c) == pytest.approx(0.0, abs=1e-12)

    def test_analytic_value(self):
        r = np.array([1.0, 0, 0, 0])
        assert mahalanobis_D(r, np.full(4, 1.0)) == pytest.approx(1.0)

    def test_inverse_weighting(self):
        r = np.array([1.0, 0, 0, 0])
        assert mahalanobis_D(r, np.full(4, 0.5)) == pytest.approx(2.0)


class TestChi2:
    def test_known_thresholds(self):
        assert chi2_ppf(0.99, 4) == pytest.approx(13.2767, rel=0.02)
        assert chi2_ppf(0.99, 1) == pytest.approx(6.6349, rel=0.02)

    def test_matches_scipy(self):
        # scipy is a test-only cross-check; rg_core itself is stdlib+numpy so it
        # can run inside ROS 2 nodes without extra system deps.
        for k in (1, 2, 3, 4, 6, 9):
            assert chi2_ppf(0.99, k) == pytest.approx(sps.chi2.ppf(0.99, k), rel=0.02)

    def test_threshold_monotone_in_k(self):
        th = [chi2_threshold(k, 0.01) for k in range(1, 11)]
        assert all(b > a for a, b in zip(th, th[1:]))

    def test_sf_consistency(self):
        for k in (1, 2, 4, 8):
            for p in (0.1, 0.5, 0.9, 0.99):
                d = sps.chi2.ppf(1 - p, k)  # P(chi2_k > d) == p
                assert chi2_sf(d, k) == pytest.approx(p, rel=0.05, abs=0.01)

    def test_sf_bounds(self):
        assert chi2_sf(0.0, 4) == pytest.approx(1.0)
        assert chi2_sf(1e6, 4) < 1e-8


class TestCheckDivergence:
    def test_clean_step_not_flagged(self):
        mean = np.array([0.42, 0.10, 0.04, 0.0])
        pred = {"pose(red_cube)": (mean, np.full(4, 1e-4))}
        obs = {"pose(red_cube)": (mean + 1e-3, np.full(4, 4e-4))}
        res = check_divergence(
            pred, obs, {"holding(red_cube)": True}, {"holding(red_cube)": True}
        )
        assert not res.flagged
        assert res.discrete_mismatches == []

    def test_discrete_mismatch_flagged(self):
        mean = np.zeros(4)
        pred = {"pose(red_cube)": (mean, np.full(4, 1e-4))}
        obs = {"pose(red_cube)": (mean, np.full(4, 1e-4))}
        res = check_divergence(
            pred, obs, {"holding(red_cube)": True}, {"holding(red_cube)": False}
        )
        assert res.flagged
        assert "holding(red_cube)" in res.discrete_mismatches

    def test_pose_shift_flagged(self):
        mean = np.zeros(4)
        pred = {"pose(red_cube)": (mean, np.full(4, 1e-4))}  # sigma = 1 cm
        obs = {"pose(red_cube)": (mean + np.array([0.06, 0, 0, 0]), np.full(4, 1e-4))}
        # D = 0.06^2 / 2e-4 = 18 > chi2_ppf(0.99, 4) = 13.28
        res = check_divergence(pred, obs, {}, {})
        assert res.flagged
        g = res.groups[0]
        assert g.k == 4 and g.D > g.threshold

    def test_bonferroni_within_step(self):
        # D = 15 sits between the single-group threshold (13.28) and the
        # Bonferroni-corrected threshold for 4 groups (~16.9): flagged alone,
        # not flagged when four variable groups share the step's alpha budget.
        mean = np.zeros(4)
        off = np.array([np.sqrt(15.0 * 2e-4), 0, 0, 0])
        pred1 = {"pose(red_cube)": (mean, np.full(4, 1e-4))}
        obs1 = {"pose(red_cube)": (off, np.full(4, 1e-4))}
        assert check_divergence(pred1, obs1, {}, {}).flagged

        pred4 = {f"pose(o{i})": (mean, np.full(4, 1e-4)) for i in range(4)}
        obs4 = {
            f"pose(o{i})": (mean if i else off, np.full(4, 1e-4)) for i in range(4)
        }
        assert not check_divergence(pred4, obs4, {}, {}).flagged

    def test_unobserved_recorded_not_flagged(self):
        mean = np.zeros(4)
        pred = {"pose(ghost)": (mean, np.full(4, 1e-4))}
        res = check_divergence(pred, {}, {}, {})
        assert not res.flagged
        assert "pose(ghost)" in res.unobserved

    def test_false_alarm_rate_fault_free(self):
        """Spec 6.2: tune to a false-alarm rate under 2% on fault-free steps."""
        rng = np.random.default_rng(7)
        flags, steps = 0, 3000
        mean = np.array([0.4, 0.1, 0.05, 0.3])
        cov_p, cov_o = np.full(4, 1e-4), np.full(4, 4e-4)
        sd = np.sqrt(cov_o)
        for _ in range(steps):
            pred = {"pose(a)": (mean, cov_p), "pose(b)": (mean + 0.2, cov_p)}
            obs = {n: (m + rng.normal(0, sd), cov_o) for n, (m, _) in pred.items()}
            res = check_divergence(
                pred, obs, {"holding(a)": True}, {"holding(a)": True}
            )
            flags += int(res.flagged)
        assert flags / steps < 0.02

    def test_verified_confidence_relation(self):
        """Spec 6.7: verified iff independent-signal confidence >= 0.9, i.e.
        confidence = chi2_sf(D, k) >= 0.9  <=>  D <= chi2_ppf(0.1, k)."""
        k = 4
        d90 = chi2_ppf(0.10, k)
        assert chi2_sf(d90, k) == pytest.approx(0.90, abs=0.02)
        assert chi2_sf(d90 + 0.5, k) < 0.90
