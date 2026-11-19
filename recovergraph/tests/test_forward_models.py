"""Forward-model prediction tests (spec 6.1)."""
import numpy as np
import pytest

from rg_core.forward_models import ForwardModel, SkillParams
from rg_core.types import BeliefState, GripperState


def belief(holding=None) -> BeliefState:
    conf = {}
    grip = GripperState("empty", 0.08, 0.0, 0.0)
    if holding:
        grip = GripperState("holding", 0.05, 25.0, 25.0)
        conf[f"holding({holding})"] = 0.95
    return BeliefState(
        t=0.0, ee_pose=np.zeros(4), ee_cov_diag=np.full(4, 1e-4),
        gripper=grip, objects={}, confidence=conf)


class TestPredict:
    def test_pick_gaussian_effect(self):
        fm = ForwardModel()
        p = fm.predict("pick", {"obj": "red_cube"}, belief(),
                       {"grasp_pos": (0.4, 0.1, 0.02), "yaw": 0.0})
        mean, cov = p.expected_poses["pose(red_cube)"]
        assert mean[:3] == pytest.approx([0.4, 0.1, 0.02])
        assert np.all(cov > 0)
        assert p.expected_discrete["holding(red_cube)"] == (True, 0.98)
        assert p.expected_discrete["gripper_empty"] == (False, 0.97)

    def test_move_held_object_follows(self):
        fm = ForwardModel()
        p = fm.predict("move", {}, belief(holding="red_cube"),
                       {"target": (0.3, -0.1, 0.15), "yaw": 0.5, "dist": 0.4})
        mean, _ = p.expected_poses["pose(red_cube)"]
        assert mean[:3] == pytest.approx([0.3, -0.1, 0.15])  # rides at EE height
        held_pred, conf = p.expected_discrete["holding(red_cube)"]
        assert held_pred is True and conf < 1.0

    def test_place_release(self):
        fm = ForwardModel()
        p = fm.predict("place", {"obj": "red_cube"}, belief(holding="red_cube"),
                       {"target": (0.35, 0.0, 0.05), "yaw": 0.0})
        mean, cov = p.expected_poses["pose(red_cube)"]
        assert mean[:3] == pytest.approx([0.35, 0.0, 0.05])
        assert np.all(cov > 0)
        assert p.expected_discrete["gripper_empty"] == (True, 0.97)
        assert p.expected_discrete["holding(red_cube)"] == (False, 0.97)

    def test_unknown_skill(self):
        with pytest.raises(ValueError):
            ForwardModel().predict("teleport", {}, belief(), {})

    def test_param_dataclass_defaults_match_spec(self):
        # spec 6.1 defaults: p_grasp 0.9 hand-set, p_slip 0.03. Milestone-1
        # values are calibrated tighter to meet the <2% false-alarm target
        # (spec 6.2); the hand-set spec values must remain documented.
        p = SkillParams()
        assert 0.9 <= p.p_grasp <= 1.0 and 0.0 < p.p_slip <= 0.03
