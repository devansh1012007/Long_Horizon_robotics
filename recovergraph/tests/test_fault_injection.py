"""Fault injection tests (spec 10.1): reproducibility, label honesty,
parameter ranges, stratification."""
import numpy as np
import pytest

from rg_core.types import INJECTED_LABELS
from rgsim.fault_injector import (DIAGNOSABLE, PARAM_RANGES,
                                  episode_schedule, sample_schedule)


class TestSchedule:
    def test_reproducible_from_seed(self):
        a = episode_schedule(42, 5, "single", "middle")
        b = episode_schedule(42, 5, "single", "middle")
        assert [e.to_dict() for e in a] == [e.to_dict() for e in b]

    def test_labels_follow_injected_mechanism(self):
        """Spec 10.1: ground-truth label comes from the injected mechanism."""
        rng = np.random.default_rng(0)
        for _ in range(200):
            evs = sample_schedule(rng, 5, DIAGNOSABLE + ["stale_dependency"], 1)
            for e in evs:
                assert e.label in INJECTED_LABELS
                assert e.label == e.fault  # v1: label == mechanism

    def test_parameter_ranges_match_spec(self):
        rng = np.random.default_rng(1)
        for _ in range(100):
            for fault, ranges in PARAM_RANGES.items():
                params = {}
                from rgsim.fault_injector import sample_fault_params
                params = sample_fault_params(rng, fault)
                for k, (lo, hi) in ranges.items():
                    assert lo <= params[k] <= hi, (fault, k)
        # spec values: miss offset 2-6 cm, slip friction x0.3-0.6,
        # pose 2-8 cm / 5-20 deg, occlusion 30-80%, displacement 3-15 cm
        assert PARAM_RANGES["f_miss"]["offset"] == (0.02, 0.06)
        assert PARAM_RANGES["f_slip"]["friction_mult"] == (0.3, 0.6)
        assert PARAM_RANGES["f_pose"]["pos"] == (0.02, 0.08)
        assert PARAM_RANGES["f_perception"]["occl_frac"] == (0.3, 0.8)
        assert PARAM_RANGES["f_collision"]["shift"] == (0.03, 0.15)

    @pytest.mark.parametrize("strat,lo_frac,hi_frac", [
        ("early", 0.0, 0.34), ("middle", 0.29, 0.70), ("late", 0.59, 1.0)])
    def test_stratification(self, strat, lo_frac, hi_frac):
        rng = np.random.default_rng(2)
        for _ in range(50):
            evs = sample_schedule(rng, 10, DIAGNOSABLE, 1, stratify=strat)
            frac = evs[0].step / 10
            assert lo_frac <= frac <= hi_frac

    def test_compound_two_distinct_steps(self):
        rng = np.random.default_rng(3)
        evs = sample_schedule(rng, 6, DIAGNOSABLE, 2)
        assert len(evs) == 2
        assert evs[0].step != evs[1].step

    def test_none_condition_empty(self):
        assert episode_schedule(9, 4, "none") == []
