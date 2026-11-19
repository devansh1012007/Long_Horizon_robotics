"""Statistics (spec 10.6): paired bootstrap CIs, Wilcoxon signed-rank,
McNemar exact test, Holm-Bonferroni correction. Paired by seed.

Scipy is used here (experiments only); rg_core itself stays stdlib+numpy.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import stats as sps


def paired_bootstrap_ci(diffs, n_resamples: int = 10000, alpha: float = 0.05,
                        seed: int = 0) -> tuple:
    """95% CI of mean(diff) by paired bootstrap (spec 10.6: 10,000 resamples).
    Returns (mean, lo, hi)."""
    d = np.asarray(diffs, float)
    rng = np.random.default_rng(seed)
    n = len(d)
    means = np.empty(n_resamples)
    for i in range(n_resamples):
        idx = rng.integers(0, n, n)
        means[i] = d[idx].mean()
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(d.mean()), float(lo), float(hi)


def mcnemar_exact(x, y) -> tuple:
    """McNemar test on paired binary outcomes. x, y: 0/1 arrays of success.
    Returns (b, c, p_two_sided)."""
    x = np.asarray(x).astype(int)
    y = np.asarray(y).astype(int)
    b = int(np.sum((x == 1) & (y == 0)))   # x success, y fail
    c = int(np.sum((x == 0) & (y == 1)))   # x fail, y success
    if b + c == 0:
        return b, c, 1.0
    p = sps.binomtest(min(b, c), b + c, 0.5).pvalue * 2
    return b, c, float(min(1.0, p))


def wilcoxon_signed_rank(x, y) -> tuple:
    """Wilcoxon signed-rank on paired observations (fallback for non-binary
    metrics such as extra actions). Returns (stat, p)."""
    res = sps.wilcoxon(x, y, zero_method="wilcox")
    return float(res.statistic), float(res.pvalue)


def holm_bonferroni(pvals: list) -> list:
    """Holm-Bonferroni adjusted p-values (spec 10.6: across ablations)."""
    p = np.asarray(pvals, float)
    order = np.argsort(p)
    m = len(p)
    adjusted = np.empty(m)
    prev = 0.0
    for rank, idx in enumerate(order):
        adj = min(1.0, (m - rank) * p[idx])
        adj = max(adj, prev)          # enforce monotonicity
        adjusted[idx] = adj
        prev = adj
    return [float(a) for a in adjusted]


def wilson_ci(successes: int, n: int, alpha: float = 0.05) -> tuple:
    """Wilson score interval for a proportion (per-cell reporting)."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    z = sps.norm.ppf(1 - alpha / 2)
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (p, max(0.0, (centre - spread) / denom),
            min(1.0, (centre + spread) / denom))


def slope_least_squares(xs: list, ys: list) -> float:
    """OLS slope for success-vs-horizon curves (go/no-go rule 2)."""
    x = np.asarray(xs, float)
    y = np.asarray(ys, float)
    if len(x) < 2 or np.all(x == x[0]):
        return 0.0
    return float(np.polyfit(x, y, 1)[0])


def expected_calibration_error(confidences: list, correct: list,
                               n_bins: int = 10) -> float:
    """ECE of diagnosis posteriors (spec 10.5 'Calibration')."""
    conf = np.asarray(confidences, float)
    cor = np.asarray(correct, float)
    ece = 0.0
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        m = (conf > lo) & (conf <= hi) if b else (conf >= 0) & (conf <= hi)
        if m.sum() == 0:
            continue
        ece += m.mean() * abs(cor[m].mean() - conf[m].mean())
    return float(ece)


def brier_score(confidences: list, correct: list) -> float:
    conf = np.asarray(confidences, float)
    cor = np.asarray(correct, float)
    return float(np.mean((conf - cor) ** 2))
