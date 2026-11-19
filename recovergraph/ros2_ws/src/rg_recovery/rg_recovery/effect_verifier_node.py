"""effect_verifier node (spec Section 7): ledger + divergence detection.

Subscribes /belief/state and /exec/events; runs rg_core.divergence per step;
publishes /ledger/entry; serves /verify_effect for the verified state update
(spec 6.7).
"""
import json

import numpy as np
import rclpy
from rclpy.node import Node

from rg_msgs.msg import BeliefState, LedgerEntry
from rg_msgs.srv import VerifyEffect
from rg_core.divergence import check_divergence, chi2_sf, mahalanobis_D


class EffectVerifierNode(Node):

    def __init__(self):
        super().__init__("effect_verifier")
        self.declare_parameter("alpha", 0.005)
        self.declare_parameter("verified_conf", 0.9)
        self.alpha = float(self.get_parameter("alpha").value)
        self.verified_conf = float(self.get_parameter("verified_conf").value)
        self.sub_belief = self.create_subscription(
            BeliefState, "/belief/state", self.cb_belief, 10)
        self.pub_ledger = self.create_publisher(LedgerEntry, "/ledger/entry",
                                                10)
        self.srv = self.create_service(VerifyEffect, "/verify_effect",
                                       self.cb_verify)
        self.latest_belief: BeliefState | None = None
        self.get_logger().info("effect_verifier ready")

    def cb_belief(self, msg: BeliefState):
        self.latest_belief = msg

    def cb_verify(self, request, response):
        entry = request.entry
        expected = json.loads(entry.expected_effects_json)
        observed = json.loads(entry.observed_state_json)
        exp_poses = {v: (np.array(d["mean"]), np.array(d["cov_diag"]))
                     for v, d in expected.items() if "cov_diag" in d}
        obs_poses = {v: (np.array(d["mean"]), np.array(d["cov_diag"]))
                     for v, d in observed.items() if "cov_diag" in d}
        payload = json.loads(entry.entry_json or "{}")
        exp_disc = payload.get("expected_discrete", {})
        obs_disc = payload.get("observed_discrete", {})
        res = check_divergence(exp_poses, obs_poses, exp_disc, obs_disc,
                               alpha=self.alpha)
        # camera-only confidence for the verified state update (spec 6.7):
        # agreement inside the verification tolerance
        worst = 0.0
        tol = np.array([0.03**2, 0.03**2, 0.03**2, 0.2**2])
        for v in exp_poses:
            if v in obs_poses:
                d = mahalanobis_D(obs_poses[v][0] - exp_poses[v][0], tol)
                worst = max(worst, d)
        conf = float(chi2_sf(worst, 4))
        mismatch = bool(res.discrete_mismatches)
        response.verified = (conf >= self.verified_conf) and not res.flagged \
            and not mismatch
        response.confidence = conf
        response.reason = "ok" if response.verified else \
            f"D_tol={worst:.2f}, flagged={res.flagged}, " \
            f"mismatches={res.discrete_mismatches}"
        return response


def main(args=None):
    rclpy.init(args=args)
    node = EffectVerifierNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
