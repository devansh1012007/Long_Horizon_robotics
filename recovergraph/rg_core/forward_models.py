"""Forward models P(s_{t+1} | s_t, a_t) as Gaussian effects around nominal
outcomes (spec 6.1). Initial parameters hand-set; calibrated on the
fault-free calibration set in Phase 2.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .types import BeliefState, Prediction


@dataclass
class SkillParams:
    p_grasp: float = 0.98          # nominal grasp success (calibrated Phase 2)
    p_slip: float = 0.008          # per-move slip probability (calibrated Phase 2)
    ee_pos_sigma: float = 0.008    # EE tracking std (m)
    ee_yaw_sigma: float = 0.03     # EE yaw std (rad)
    grasp_offset_sigma: float = 0.010
    place_pos_sigma: float = 0.010
    place_yaw_sigma: float = 0.05
    grip_conf: float = 0.97        # confidence for discrete release effects


def _pose_cov(pos_sigma, yaw_sigma) -> np.ndarray:
    return np.array([pos_sigma**2, pos_sigma**2, pos_sigma**2, yaw_sigma**2])


class ForwardModel:
    """Predicts intended effects from the commanded action and current belief.

    `command` carries the commanded targets:
      pick:  {"grasp_pos": (3,), "yaw": float}
      move:  {"target": (3,), "yaw": float, "dist": float}
      place: {"target": (3,), "yaw": float}
    """

    def __init__(self, params: SkillParams | None = None):
        self.p = params or SkillParams()

    def predict(self, skill: str, args: dict, belief: BeliefState,
                command: dict) -> Prediction:
        pred = Prediction()
        if skill == "pick":
            obj = args["obj"]
            # a successful grasp leaves the object at its own position
            # (off-center grasps change the EE-to-object offset, not the
            # object pose); predicted yaw is the object's believed yaw
            est = belief.objects.get(obj)
            mean = est.pose.copy() if est is not None else \
                np.asarray(command["grasp_pos"], float)
            cov = _pose_cov(self.p.grasp_offset_sigma, self.p.ee_yaw_sigma)
            pred.expected_poses[f"pose({obj})"] = (mean, cov)
            pred.expected_discrete[f"holding({obj})"] = (True, self.p.p_grasp)
            pred.expected_discrete["gripper_empty"] = (False, self.p.grip_conf)
        elif skill == "move":
            tgt = np.asarray(command["target"], float)
            yaw = float(command.get("yaw", 0.0))
            dist = float(command.get("dist", 0.2))
            # tracking covariance grows with travel distance
            scale = 1.0 + dist
            pred.expected_poses["pose(ee)"] = (
                np.array([tgt[0], tgt[1], tgt[2], yaw]),
                _pose_cov(self.p.ee_pos_sigma * scale, self.p.ee_yaw_sigma * scale))
            held = self._held_object(belief)
            if held is not None:
                slip_conf = (1.0 - self.p.p_slip) ** max(1, int(dist / 0.15))
                pred.expected_discrete[f"holding({held})"] = (True, slip_conf)
                # object rides at EE height (grasp offset has zero z-component)
                pred.expected_poses[f"pose({held})"] = (
                    np.array([tgt[0], tgt[1], tgt[2], yaw]),
                    _pose_cov(self.p.grasp_offset_sigma * scale,
                              self.p.ee_yaw_sigma * scale))
        elif skill == "place":
            obj = args["obj"]
            tgt = np.asarray(command["target"], float)
            yaw = float(command.get("yaw", 0.0))
            cov = _pose_cov(
                float(np.hypot(self.p.place_pos_sigma, self.p.ee_pos_sigma)),
                self.p.place_yaw_sigma)
            pred.expected_poses[f"pose({obj})"] = (
                np.array([tgt[0], tgt[1], tgt[2], yaw]), cov)
            pred.expected_discrete["gripper_empty"] = (True, self.p.grip_conf)
            pred.expected_discrete[f"holding({obj})"] = (False, self.p.grip_conf)
        else:
            raise ValueError(f"unknown skill {skill}")
        return pred

    @staticmethod
    def _held_object(belief: BeliefState):
        for var, conf in belief.confidence.items():
            if var.startswith("holding(") and conf > 0.5:
                return var[len("holding("):-1]
        return None
