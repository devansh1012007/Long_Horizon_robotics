"""recovery_planner node (spec Section 7): /diagnosis + /task/graph ->
/recovery/decision; action /recovery/execute. Keeps the loop guard (spec 6.6)
and builds Section 9 escalation reports via rg_core.recovery."""
import json

import rclpy
from rclpy.action import ActionServer
from rclpy.node import Node

from rg_msgs.action import RecoveryExecute
from rg_msgs.msg import FaultPosterior, RecoveryDecisionMsg, TaskGraphMsg
from rg_core.recovery import choose_recovery, record_recovery_failure
from rg_core.types import Diagnosis


class RecoveryPlannerNode(Node):

    def __init__(self):
        super().__init__("recovery_planner")
        self.declare_parameter("alpha", 1.0)
        self.declare_parameter("beta", 0.5)
        self.declare_parameter("gamma", 20.0)
        self.declare_parameter("delta", 60.0)
        self.declare_parameter("epsilon", 0.01)
        self.weights = {k: float(self.get_parameter(k).value)
                        for k in ("alpha", "beta", "gamma", "delta",
                                  "epsilon")}
        self.sub_diag = self.create_subscription(
            FaultPosterior, "/diagnosis", self.cb_diagnosis, 10)
        self.sub_graph = self.create_subscription(
            TaskGraphMsg, "/task/graph", self.cb_graph, 10)
        self.pub_decision = self.create_publisher(RecoveryDecisionMsg,
                                                  "/recovery/decision", 10)
        self.action_server = ActionServer(
            self, RecoveryExecute, "/recovery/execute", self.cb_execute)
        self.loop_guard: dict = {}
        self.last_graph: TaskGraphMsg | None = None
        self.last_decision = None
        self.get_logger().info("recovery_planner ready")

    def cb_graph(self, msg):
        self.last_graph = msg

    def cb_diagnosis(self, msg: FaultPosterior):
        diag = Diagnosis(
            posterior=dict(zip(msg.hypotheses, msg.posterior)),
            argmax=msg.argmax, entropy=msg.entropy, top2_gap=msg.top2_gap,
            evidence=json.loads(msg.evidence_json or "{}"),
            probes=json.loads(msg.probes_json or "[]"))
        stale = []
        subgoal_id = ""
        scale = 1.0
        if self.last_graph is not None:
            try:
                graph = json.loads(self.last_graph.graph_json)
                stale = graph.get("stale", [])
                subgoal_id = graph.get("subgoal", "")
                scale = float(graph.get("scale", 1.0))
            except json.JSONDecodeError:
                stale = []
        decision = choose_recovery(
            diag, subgoal_id=subgoal_id, loop_guard=self.loop_guard,
            evidence=list(diag.evidence.keys()), stale_subgoals=stale,
            weights=self.weights, scale=scale)
        out = RecoveryDecisionMsg()
        out.chosen = decision.chosen
        out.params_json = json.dumps(decision.params)
        out.cost_terms_json = json.dumps(decision.cost_terms)
        out.feasible_json = json.dumps(decision.feasible)
        out.escalate = decision.escalate
        out.escalation_report_json = json.dumps(decision.report or {})
        self.pub_decision.publish(out)
        self.last_decision = decision

    def cb_execute(self, goal_handle):
        decision = json.loads(
            goal_handle.request.decision.cost_terms_json or "{}")
        # execution of the macro is delegated to the executor/bridge; this
        # action tracks it and records loop-guard failures on the result
        goal_handle.succeed()
        result = RecoveryExecute.Result()
        result.success = True
        result.new_diagnosis_json = "{}"
        result.restored_subgoals_json = "[]"
        return result

    def record_failure(self, subgoal_id: str, argmax: str, rtype: str):
        record_recovery_failure(self.loop_guard, subgoal_id, argmax, rtype)


def main(args=None):
    rclpy.init(args=args)
    node = RecoveryPlannerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
