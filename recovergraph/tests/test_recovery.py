"""Recovery candidate generation and cost-based selection tests (spec 6.4-6.6)."""
import pytest

from rg_core.diagnosis import diagnose
from rg_core.recovery import (
    AFFORDANCES,
    choose_recovery,
    record_recovery_failure,
)
from rg_core.types import Diagnosis


def post_for(fault, p=0.9):
    rest = (1.0 - p) / 6.0
    return {f: (p if f == fault else rest)
            for f in ("f_none", "f_miss", "f_slip", "f_pose",
                      "f_collision", "f_perception", "f_controller")}


class TestAffordances:
    def test_plain_retry_disallowed_for_miss(self):
        """Spec 6.4: 'plain retry of the same grasp is disallowed until cause
        resolved'."""
        assert "retry_modified" not in AFFORDANCES["f_miss"]

    def test_escalation_fallback_always_exists(self):
        """With every autonomous affordance of the argmax fault loop-guarded
        out, the decision must fall back to escalation (spec 6.6)."""
        for fault in ("f_miss", "f_slip", "f_pose", "f_collision",
                      "f_perception", "f_controller"):
            d = diagnose({"gripper_closed_narrow": True,
                          "grip_force_drop": True,
                          "tracked_obj_final_pose_off": True,
                          "controller_timeout_abort": True,
                          "non_target_object_moved": True,
                          "low_visibility_ee_nominal": True}, "move")
            d = Diagnosis.from_posterior(post_for(fault))
            lg = {}
            for r in AFFORDANCES[fault]:
                record_recovery_failure(lg, "g1", fault, r)
                record_recovery_failure(lg, "g1", fault, r)
            dec = choose_recovery(d, "g1", loop_guard=lg)
            assert dec.escalate and dec.chosen == "escalate", fault


class TestSelection:
    def test_miss_prefers_regrasp_family(self):
        d = diagnose({"gripper_closed_narrow": True}, "pick")
        assert d.argmax == "f_miss"
        dec = choose_recovery(d, "g1")
        assert dec.chosen in ("regrasp", "reobserve_retry_modified")
        assert not any(t == "retry_modified" for t, _ in dec.feasible)

    def test_collision_vetoes_rollback(self):
        d = diagnose({"non_target_object_moved": True}, "place")
        assert d.argmax == "f_collision"
        dec = choose_recovery(d, "g2")
        names = [t for t, _ in dec.feasible]
        assert "rollback" not in names  # P(unsafe) = .05 > epsilon: hard veto
        assert dec.chosen in ("reobserve_replan", "replan_motion")

    def test_perception_gets_nonphysical_recovery(self):
        d = diagnose({"low_visibility_ee_nominal": True}, "pick")
        assert d.argmax == "f_perception"
        dec = choose_recovery(d, "g1")
        assert dec.chosen == "reobserve_redetect"

    def test_controller_gets_param_retry(self):
        d = diagnose({"controller_timeout_abort": True}, "place")
        dec = choose_recovery(d, "g2")
        assert dec.chosen == "retry_modified"


class TestLoopGuard:
    def test_repeated_failures_remove_recovery_type(self):
        d = diagnose({"gripper_closed_narrow": True}, "pick")
        lg = {}
        first = choose_recovery(d, "g1", loop_guard=lg)
        assert first.chosen == "reobserve_retry_modified"
        record_recovery_failure(lg, "g1", "f_miss", first.chosen)
        record_recovery_failure(lg, "g1", "f_miss", first.chosen)
        second = choose_recovery(d, "g1", loop_guard=lg)
        assert second.chosen != first.chosen
        assert second.chosen == "regrasp"
        record_recovery_failure(lg, "g1", "f_miss", second.chosen)
        record_recovery_failure(lg, "g1", "f_miss", second.chosen)
        third = choose_recovery(d, "g1", loop_guard=lg)
        assert third.escalate and third.chosen == "escalate"
        assert third.report["why_escalating"] == \
            "all safe options exceed autonomy risk threshold"


class TestEscalationReport:
    def test_report_matches_section9_schema(self):
        d = diagnose({"gripper_closed_narrow": True}, "pick")
        lg = {}
        for r in ("reobserve_retry_modified", "regrasp"):
            record_recovery_failure(lg, "g1", "f_miss", r)
            record_recovery_failure(lg, "g1", "f_miss", r)
        dec = choose_recovery(d, "g1", loop_guard=lg,
                              evidence=["grip force dropped 62% at t=3.2s"],
                              stale_subgoals=["g3", "g4"])
        assert dec.escalate
        rep = dec.report
        assert set(rep) == {"failure_reason", "evidence", "alternatives",
                            "stale_subgoals", "why_escalating"}
        assert "p=" in rep["failure_reason"]
        assert rep["evidence"] == ["grip force dropped 62% at t=3.2s"]
        assert rep["stale_subgoals"] == ["g3", "g4"]

    def test_cost_escalation_with_alternatives(self):
        d = diagnose({"gripper_closed_narrow": True}, "pick")
        dec = choose_recovery(d, "g1", weights={"gamma": 1000.0})
        assert dec.escalate
        assert dec.report["why_escalating"] == \
            "all safe options exceed escalation cost"
        assert dec.report["alternatives"]  # feasible options listed for operator

    def test_cost_terms_recorded(self):
        d = diagnose({"grip_force_drop": True}, "move")
        dec = choose_recovery(d, "g1")
        assert {"T", "C", "P_fail", "P_unsafe", "cost"} <= set(dec.cost_terms)
        assert dec.cost_terms["P_unsafe"] <= 0.01
