"""fault_diagnoser node (spec Section 7): ledger entries -> /diagnosis.

Evidence features arrive in the ledger entry payload (extracted from gripper
telemetry, tracker, controller status, scene diff - spec 6.3); the node runs
rg_core.diagnosis and serves /probe_suggestion with expected information gain.
"""
import json

import rclpy
from rclpy.node import Node

from rg_msgs.msg import FaultPosterior, LedgerEntry
from rg_msgs.srv import ProbeSuggestion
from rg_core.diagnosis import (PROBES, diagnose, expected_information_gain,
                               suggest_probe)


class FaultDiagnoserNode(Node):

    def __init__(self):
        super().__init__("fault_diagnoser")
        self.declare_parameter("gap_threshold", 0.2)
        self.declare_parameter("entropy_threshold", 1.2)
        self.gap_threshold = float(self.get_parameter("gap_threshold").value)
        self.entropy_threshold = float(
            self.get_parameter("entropy_threshold").value)
        self.sub_ledger = self.create_subscription(
            LedgerEntry, "/ledger/entry", self.cb_ledger, 10)
        self.pub_diag = self.create_publisher(FaultPosterior, "/diagnosis", 10)
        self.srv = self.create_service(ProbeSuggestion, "/probe_suggestion",
                                       self.cb_probe)
        self.get_logger().info("fault_diagnoser ready")

    def cb_ledger(self, msg: LedgerEntry):
        if not msg.divergence_flagged:
            return
        entry = json.loads(msg.entry_json) if msg.entry_json else {}
        evidence = entry.get("evidence", {})
        skill = entry.get("skill", "pick")
        diag = diagnose(evidence, skill)
        out = FaultPosterior()
        out.hypotheses = list(diag.posterior.keys())
        out.posterior = [float(v) for v in diag.posterior.values()]
        out.argmax = diag.argmax
        out.entropy = diag.entropy
        out.top2_gap = diag.top2_gap
        out.evidence_json = json.dumps(evidence)
        out.probes_json = json.dumps(diag.probes)
        out.posterior_json = json.dumps(diag.posterior)
        self.pub_diag.publish(out)

    def cb_probe(self, request, response):
        posterior = json.loads(request.posterior.posterior_json or "{}")
        if not posterior:
            response.probe_needed = False
            return response
        best, best_ig = None, -1.0
        ambiguous = (request.posterior.top2_gap < self.gap_threshold
                     or request.posterior.entropy > self.entropy_threshold)
        if ambiguous:
            for name, table in PROBES.items():
                ig = expected_information_gain(posterior, table)
                if ig > best_ig:
                    best, best_ig = name, ig
        if best is None:
            response.probe_needed = False
            response.probe_type = ""
            response.expected_information_gain = 0.0
        else:
            response.probe_needed = True
            response.probe_type = best
            response.params_json = "{}"
            response.expected_information_gain = float(best_ig)
        return response


def main(args=None):
    rclpy.init(args=args)
    node = FaultDiagnoserNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
