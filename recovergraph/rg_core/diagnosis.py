"""Causal fault diagnosis: interpretable naive Bayes (spec 6.3).

P(f_i | e_t, a_t, h_t) proportional to P(e_t | f_i, a_t) * P(f_i | a_t, h_t).

The likelihood table is transcribed verbatim from spec Section 6.3 and guarded
by tests/test_diagnosis.py. Evidence features are treated as conditionally
independent given the fault (naive Bayes, v1). Priors depend on the skill.
Disambiguating probes (re-observe from another viewpoint, gentle gripper
squeeze) are suggested when the posterior is ambiguous, chosen by expected
information gain computed from probe outcome likelihood tables.
"""
from __future__ import annotations

import math

from .types import FAULTS, Diagnosis

# P(evidence feature observed | fault) -- spec 6.3 table, verbatim.
LIKELIHOODS = {
    "gripper_closed_narrow": {
        "f_none": 0.02, "f_miss": 0.90, "f_slip": 0.05, "f_pose": 0.05,
        "f_collision": 0.10, "f_perception": 0.05, "f_controller": 0.05,
    },
    "grip_force_drop": {
        "f_none": 0.02, "f_miss": 0.02, "f_slip": 0.90, "f_pose": 0.05,
        "f_collision": 0.30, "f_perception": 0.02, "f_controller": 0.05,
    },
    "tracked_obj_final_pose_off": {
        "f_none": 0.03, "f_miss": 0.02, "f_slip": 0.05, "f_pose": 0.90,
        "f_collision": 0.15, "f_perception": 0.20, "f_controller": 0.10,
    },
    "low_visibility_ee_nominal": {
        "f_none": 0.02, "f_miss": 0.05, "f_slip": 0.05, "f_pose": 0.02,
        "f_collision": 0.02, "f_perception": 0.90, "f_controller": 0.02,
    },
    "controller_timeout_abort": {
        "f_none": 0.01, "f_miss": 0.05, "f_slip": 0.05, "f_pose": 0.10,
        "f_collision": 0.30, "f_perception": 0.02, "f_controller": 0.95,
    },
    "non_target_object_moved": {
        "f_none": 0.01, "f_miss": 0.02, "f_slip": 0.05, "f_pose": 0.05,
        "f_collision": 0.90, "f_perception": 0.05, "f_controller": 0.05,
    },
}

# Hand-set skill-conditioned priors (spec 6.3: "Priors depend on the skill").
# Spec examples fixed by test: pick -> f_miss 0.4 dominant; move -> f_slip 0.4.
SKILL_PRIORS = {
    "pick": {
        "f_none": 0.05, "f_miss": 0.40, "f_slip": 0.10, "f_pose": 0.10,
        "f_collision": 0.10, "f_perception": 0.15, "f_controller": 0.10,
    },
    "move": {
        "f_none": 0.05, "f_miss": 0.02, "f_slip": 0.40, "f_pose": 0.15,
        "f_collision": 0.15, "f_perception": 0.10, "f_controller": 0.13,
    },
    "place": {
        "f_none": 0.05, "f_miss": 0.05, "f_slip": 0.15, "f_pose": 0.40,
        "f_collision": 0.15, "f_perception": 0.10, "f_controller": 0.10,
    },
}

# Probe outcome likelihood tables P(probe outcome | fault), columns sum to 1.
# Hand-set in v1; calibrated / replaced by a learned model in Phase 4b (A5).
PROBES = {
    "reobserve_alt_view": {
        "obj_at_source": {
            "f_none": 0.30, "f_miss": 0.80, "f_slip": 0.10, "f_pose": 0.15,
            "f_collision": 0.05, "f_perception": 0.30, "f_controller": 0.30,
        },
        "obj_displaced": {
            "f_none": 0.05, "f_miss": 0.10, "f_slip": 0.75, "f_pose": 0.25,
            "f_collision": 0.85, "f_perception": 0.25, "f_controller": 0.15,
        },
        "obj_as_expected": {
            "f_none": 0.60, "f_miss": 0.05, "f_slip": 0.05, "f_pose": 0.15,
            "f_collision": 0.05, "f_perception": 0.35, "f_controller": 0.45,
        },
        "still_occluded": {
            "f_none": 0.05, "f_miss": 0.05, "f_slip": 0.10, "f_pose": 0.45,
            "f_collision": 0.05, "f_perception": 0.10, "f_controller": 0.10,
        },
    },
    "gripper_squeeze": {
        "empty_gripper": {
            "f_none": 0.10, "f_miss": 0.85, "f_slip": 0.45, "f_pose": 0.20,
            "f_collision": 0.30, "f_perception": 0.20, "f_controller": 0.10,
        },
        "grip_stable": {
            "f_none": 0.90, "f_miss": 0.15, "f_slip": 0.55, "f_pose": 0.80,
            "f_collision": 0.70, "f_perception": 0.80, "f_controller": 0.90,
        },
    },
}

GAP_THRESHOLD = 0.2        # spec 6.3: top-2 gap < 0.2 -> probe
ENTROPY_THRESHOLD = 1.2    # spec 6.3: entropy > 1.2 nats -> probe


def diagnose(evidence: dict, skill: str, tables: dict | None = None) -> Diagnosis:
    """Naive Bayes posterior over the 7 fault hypotheses.

    evidence: feature -> bool; only present (True) features multiply into the
    likelihood in v1 (documented simplification; absent features carry little
    reliable information in the hand-set table).
    tables: optional {"likelihoods": L, "priors": P} learned artifact tables
    (M5); defaults to the module tables (hand-set v1, spec-guarded)."""
    if tables is None:
        likelihoods, prior = LIKELIHOODS, SKILL_PRIORS[skill]
    else:
        likelihoods = tables.get("likelihoods") or LIKELIHOODS
        prior = (tables.get("priors") or SKILL_PRIORS)[skill]
    present = [f for f, ok in evidence.items() if ok and f in likelihoods]
    posterior = {}
    for fault in FAULTS:
        p = prior[fault]
        for feat in present:
            p *= likelihoods[feat][fault]
        posterior[fault] = p
    z = sum(posterior.values())
    posterior = {f: p / z for f, p in posterior.items()}
    return Diagnosis.from_posterior(posterior, evidence=dict(evidence))


def expected_information_gain(posterior: dict, probe_table: dict) -> float:
    """E[IG] of a probe = H(posterior) - E_outcome[H(posterior | outcome)],
    exact over the probe's outcome distribution."""
    def H(p):
        return -sum(v * math.log(v) for v in p.values() if v > 0.0)

    h0 = H(posterior)
    outcomes = list(probe_table.keys())
    p_out = {}
    for o in outcomes:
        p_out[o] = sum(posterior[f] * probe_table[o][f] for f in posterior)
    expected_h = 0.0
    for o in outcomes:
        if p_out[o] <= 0.0:
            continue
        post_o = {f: posterior[f] * probe_table[o][f] / p_out[o] for f in posterior}
        expected_h += p_out[o] * H(post_o)
    return h0 - expected_h


def suggest_probe(diagnosis: Diagnosis, gap_threshold: float = GAP_THRESHOLD,
                  entropy_threshold: float = ENTROPY_THRESHOLD):
    """Return (probe_name, expected_IG) for the max-information probe if the
    posterior is ambiguous, else None (spec 6.3 'Disambiguating actions')."""
    ambiguous = (diagnosis.top2_gap < gap_threshold
                 or diagnosis.entropy > entropy_threshold)
    if not ambiguous:
        return None
    best, best_ig = None, -math.inf
    for name, table in PROBES.items():
        ig = expected_information_gain(diagnosis.posterior, table)
        if ig > best_ig:
            best, best_ig = name, ig
    return (best, best_ig) if best is not None else None


def update_with_probe(posterior: dict, probe_table: dict, outcome: str) -> dict:
    """Bayes update of the fault posterior with a probe outcome."""
    lik = probe_table[outcome]
    post = {f: posterior[f] * lik[f] for f in posterior}
    z = sum(post.values())
    return {f: p / z for f, p in post.items()}
