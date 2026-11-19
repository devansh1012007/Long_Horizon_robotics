"""Bayesian fault-diagnosis tests (spec 6.3).

Written BEFORE the implementation, per Section 17 step 5. The likelihood table
below is transcribed verbatim from spec Section 6.3; the implementation must
match it exactly.
"""
import math

import pytest

from rg_core.diagnosis import (
    FAULTS,
    LIKELIHOODS,
    PROBES,
    SKILL_PRIORS,
    diagnose,
    expected_information_gain,
    suggest_probe,
    update_with_probe,
)
from rg_core.types import Diagnosis

SPEC_TABLE = {  # verbatim from spec 6.3: P(evidence observed | fault)
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


class TestTables:
    def test_likelihood_table_matches_spec(self):
        assert LIKELIHOODS == SPEC_TABLE

    def test_priors_are_normalized_per_skill(self):
        for skill, prior in SKILL_PRIORS.items():
            assert set(prior) == set(FAULTS)
            assert sum(prior.values()) == pytest.approx(1.0, abs=1e-9)

    def test_prior_examples_from_spec(self):
        # Spec 6.3: "Priors depend on the skill (e.g. pick: miss 0.4 of
        # failures; move: slip 0.4)."
        assert max(SKILL_PRIORS["pick"], key=SKILL_PRIORS["pick"].get) == "f_miss"
        assert SKILL_PRIORS["pick"]["f_miss"] == pytest.approx(0.4)
        assert max(SKILL_PRIORS["move"], key=SKILL_PRIORS["move"].get) == "f_slip"
        assert SKILL_PRIORS["move"]["f_slip"] == pytest.approx(0.4)


class TestDiagnose:
    def test_empty_evidence_returns_prior(self):
        d = diagnose({}, "pick")
        for f in FAULTS:
            assert d.posterior[f] == pytest.approx(SKILL_PRIORS["pick"][f], abs=1e-12)

    def test_single_feature_argmax_mapping(self):
        cases = [
            ({"gripper_closed_narrow": True}, "pick", "f_miss"),
            ({"grip_force_drop": True}, "move", "f_slip"),
            ({"tracked_obj_final_pose_off": True}, "place", "f_pose"),
            ({"low_visibility_ee_nominal": True}, "pick", "f_perception"),
            ({"controller_timeout_abort": True}, "place", "f_controller"),
            ({"non_target_object_moved": True}, "place", "f_collision"),
        ]
        for ev, skill, expected in cases:
            assert diagnose(ev, skill).argmax == expected, (ev, skill)

    def test_posterior_normalized(self):
        d = diagnose({"grip_force_drop": True, "gripper_closed_narrow": True}, "move")
        assert sum(d.posterior.values()) == pytest.approx(1.0, abs=1e-12)

    def test_combined_evidence_still_slip(self):
        d = diagnose({"grip_force_drop": True, "gripper_closed_narrow": True}, "move")
        assert d.argmax == "f_slip"

    def test_collision_beats_slip_when_object_moved(self):
        d = diagnose({"grip_force_drop": True, "non_target_object_moved": True}, "move")
        assert d.argmax == "f_collision"

    def test_entropy_and_top2_gap_consistent(self):
        d = diagnose({"tracked_obj_final_pose_off": True}, "move")
        assert 0.0 <= d.entropy <= math.log(7) + 1e-9
        ranked = sorted(d.posterior.values(), reverse=True)
        assert d.top2_gap == pytest.approx(ranked[0] - ranked[1], abs=1e-9)

    def test_stronger_evidence_reduces_entropy(self):
        # Adding fault-specific evidence (non-target moved -> f_collision) must
        # sharpen the posterior over the spec's likelihood table. Note: adding
        # controller_timeout_abort does NOT, since its row is compatible with
        # f_pose/f_collision too - a property of the spec table, not a bug.
        weak = diagnose({"tracked_obj_final_pose_off": True}, "move")
        strong = diagnose(
            {"tracked_obj_final_pose_off": True, "non_target_object_moved": True},
            "move",
        )
        assert strong.entropy < weak.entropy


class TestAmbiguityGate:
    def test_confident_diagnosis_needs_no_probe(self):
        d = diagnose({"gripper_closed_narrow": True}, "pick")
        assert d.argmax == "f_miss"
        assert d.top2_gap > 0.2
        assert suggest_probe(d) is None

    def test_entropy_gate_triggers_probe(self):
        # A controller timeout on a pick is genuinely ambiguous under the spec
        # table (controller .95, collision .30, miss .05 all plausible): the
        # posterior entropy crosses the 1.2-nat gate and a probe is requested.
        d = diagnose({"controller_timeout_abort": True}, "pick")
        assert d.entropy > 1.2  # spec 6.3: entropy > 1.2 nats
        name, ig = suggest_probe(d)
        assert name in PROBES and ig > 0

    def test_gap_gate_triggers_probe(self):
        posterior = {
            "f_none": 0.05, "f_miss": 0.40, "f_slip": 0.38, "f_pose": 0.05,
            "f_collision": 0.05, "f_perception": 0.04, "f_controller": 0.03,
        }
        d = Diagnosis.from_posterior(posterior)
        assert d.top2_gap < 0.2  # spec 6.3: top-2 gap < 0.2
        name, _ = suggest_probe(d)
        assert name in PROBES


class TestProbes:
    def test_probe_tables_normalized(self):
        # P(outcome | fault) must sum to 1 over outcomes *per fault* (columns),
        # not per outcome row.
        for probe_name, outcomes in PROBES.items():
            for outcome, lik in outcomes.items():
                assert set(lik) == set(FAULTS), (probe_name, outcome)
            for fault in FAULTS:
                col = sum(outcomes[o][fault] for o in outcomes)
                assert col == pytest.approx(1.0, abs=1e-9), (probe_name, fault)

    def test_information_gain_positive_for_uniform(self):
        uniform = {f: 1.0 / 7 for f in FAULTS}
        ig = expected_information_gain(uniform, PROBES["reobserve_alt_view"])
        assert ig > 0.01

    def test_reobserve_beats_squeeze_for_miss_vs_slip(self):
        post = {
            "f_none": 0.02, "f_miss": 0.45, "f_slip": 0.45, "f_pose": 0.02,
            "f_collision": 0.02, "f_perception": 0.02, "f_controller": 0.02,
        }
        assert expected_information_gain(post, PROBES["reobserve_alt_view"]) > \
               expected_information_gain(post, PROBES["gripper_squeeze"])

    def test_squeeze_beats_reobserve_for_none_vs_miss(self):
        post = {
            "f_none": 0.45, "f_miss": 0.45, "f_slip": 0.02, "f_pose": 0.02,
            "f_collision": 0.02, "f_perception": 0.02, "f_controller": 0.02,
        }
        assert expected_information_gain(post, PROBES["gripper_squeeze"]) > \
               expected_information_gain(post, PROBES["reobserve_alt_view"])

    def test_suggest_probe_returns_max_information_probe(self):
        post = {
            "f_none": 0.02, "f_miss": 0.45, "f_slip": 0.45, "f_pose": 0.02,
            "f_collision": 0.02, "f_perception": 0.02, "f_controller": 0.02,
        }
        name, _ = suggest_probe(Diagnosis.from_posterior(post))
        assert name == "reobserve_alt_view"

    def test_bayes_update_with_probe_outcome(self):
        post = {
            "f_none": 0.10, "f_miss": 0.45, "f_slip": 0.45, "f_pose": 0.0,
            "f_collision": 0.0, "f_perception": 0.0, "f_controller": 0.0,
        }
        new = update_with_probe(post, PROBES["reobserve_alt_view"], "obj_at_source")
        assert new["f_miss"] > 0.65
        assert sum(new.values()) == pytest.approx(1.0, abs=1e-12)
