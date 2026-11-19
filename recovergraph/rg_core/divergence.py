"""Divergence detection (spec 6.2).

Mahalanobis residual D_t = (s_obs - s_hat)^T Sigma^-1 (s_obs - s_hat),
Sigma = Sigma_pred + Sigma_obs, flagged when D_t exceeds the chi-square
quantile chi2_inv(1 - alpha, k). Discrete mismatches are flagged directly.

Implementation is stdlib + numpy only (Wilson-Hilferty approximation for the
chi-square quantile, Numerical-Recipes style incomplete gamma for the
survival function) so it can run inside ROS 2 nodes without scipy. Tests
cross-check both approximations against scipy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np


# --------------------------------------------------------------------------- #
# Statistics helpers (pure python)
# --------------------------------------------------------------------------- #
def mahalanobis_D(residual: np.ndarray, cov_diag: np.ndarray) -> float:
    """D = r^T Sigma^-1 r for a diagonal covariance."""
    r = np.asarray(residual, float)
    s = np.asarray(cov_diag, float)
    if np.any(s <= 0):
        raise ValueError("covariance entries must be positive")
    return float(np.sum((r * r) / s))


def chi2_ppf(p: float, k: int) -> float:
    """Inverse chi-square CDF via Wilson-Hilferty (k >= 1, 0 < p < 1)."""
    z = NormalDist().inv_cdf(p)
    c = 2.0 / (9.0 * k)
    return k * (1.0 - c + z * math.sqrt(c)) ** 3


def chi2_threshold(k: int, alpha: float = 0.01) -> float:
    """chi2_inv(1 - alpha, k): divergence threshold for k checked dimensions."""
    return chi2_ppf(1.0 - alpha, k)


def _gammln(x: float) -> float:
    return math.lgamma(x)


def _gser(a: float, x: float, itmax: int = 300, eps: float = 3e-10) -> float:
    """Regularized lower incomplete gamma P(a, x) by series (x < a + 1)."""
    if x <= 0.0:
        return 0.0
    ap, s, d = a, 1.0 / a, 1.0 / a
    for _ in range(itmax):
        ap += 1.0
        d *= x / ap
        s += d
        if abs(d) < abs(s) * eps:
            break
    return s * math.exp(-x + a * math.log(x) - _gammln(a))


def _gcf(a: float, x: float, itmax: int = 300, eps: float = 3e-10) -> float:
    """Regularized upper incomplete gamma Q(a, x) by Lentz continued fraction."""
    tiny = 1e-300
    b, c, d, h = x + 1.0 - a, 1.0 / tiny, 1.0 / (x + 1.0 - a), 1.0 / (x + 1.0 - a)
    for i in range(1, itmax + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return math.exp(-x + a * math.log(x) - _gammln(a)) * h


def _gammp(a: float, x: float) -> float:
    if a <= 0:
        raise ValueError("a must be positive")
    if x == 0.0:
        return 0.0
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def chi2_sf(x: float, k: int) -> float:
    """P(chi2_k > x) = Q(k/2, x/2). Used for the verified-state-update
    confidence in spec 6.7: confidence = chi2_sf(D_cam, k) >= 0.9."""
    return 1.0 - _gammp(k / 2.0, x / 2.0)


# --------------------------------------------------------------------------- #
# Divergence check
# --------------------------------------------------------------------------- #
@dataclass
class GroupCheck:
    name: str
    D: float
    threshold: float
    k: int
    flagged: bool


@dataclass
class DivergenceResult:
    flagged: bool
    groups: list = field(default_factory=list)          # [GroupCheck]
    discrete_mismatches: list = field(default_factory=list)  # [var]
    unobserved: list = field(default_factory=list)      # [var] expected but unseen
    max_D: float = 0.0

    @property
    def flagged_groups(self) -> list:
        return [g.name for g in self.groups if g.flagged]

    def to_dict(self) -> dict:
        return {
            "flagged": self.flagged,
            "groups": [g.__dict__ for g in self.groups],
            "discrete_mismatches": list(self.discrete_mismatches),
            "unobserved": list(self.unobserved),
            "max_D": self.max_D,
        }


def check_divergence(
    expected_poses: dict,
    observed_poses: dict,
    expected_discrete: dict,
    observed_discrete: dict,
    alpha: float = 0.01,
) -> DivergenceResult:
    """Per-step divergence check (spec 6.2).

    expected_poses:    var -> (mean (k,), cov_diag (k,))   predicted effects
    observed_poses:    var -> (mean (k,), cov_diag (k,))   observed estimates
    expected_discrete: var -> bool                        e.g. holding(red_cube)
    observed_discrete: var -> bool

    Groups sharing a step share the alpha budget (Bonferroni within step) to
    keep the per-step false-alarm rate near alpha (spec target: < 2%).
    """
    common = [v for v in expected_poses if v in observed_poses]
    unobserved = [v for v in expected_poses if v not in observed_poses]
    alpha_eff = alpha / max(1, len(common))
    groups = []
    for v in common:
        mu_p, cov_p = expected_poses[v]
        mu_o, cov_o = observed_poses[v]
        r = np.asarray(mu_o, float) - np.asarray(mu_p, float)
        s = np.asarray(cov_p, float) + np.asarray(cov_o, float)
        k = len(s)
        D = mahalanobis_D(r, s)
        thr = chi2_threshold(k, alpha_eff)
        groups.append(GroupCheck(v, D, thr, k, bool(D > thr)))
    mismatches = [
        v for v, expected in expected_discrete.items()
        if v in observed_discrete and observed_discrete[v] != expected
    ]
    flagged = any(g.flagged for g in groups) or bool(mismatches)
    max_D = max((g.D for g in groups), default=0.0)
    return DivergenceResult(
        flagged=flagged, groups=groups, discrete_mismatches=mismatches,
        unobserved=unobserved, max_D=max_D,
    )
