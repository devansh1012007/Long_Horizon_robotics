"""safety_supervisor node (spec 6.8): independent process with veto power.

Runs rg_core.safety.SafetySupervisor in this node only; its constraints are
never traded against cost. Provides /check_trajectory (the scene travels in
the trajectory payload: waypoints + objects + held) and watches
/planned_trajectory, publishing /safety/verdict.
"""
import json

import numpy as np
import rclpy
from rclpy.node import Node

from rg_msgs.msg import SafetyVerdict
from rg_msgs.srv import CheckTrajectory
from rg_core.safety import SafetyLimits, SafetySupervisor


class SafetySupervisorNode(Node):

    def __init__(self):
        super().__init__("safety_supervisor")
        self.declare_parameter("workspace_min", [-0.45, -0.45, 0.005])
        self.declare_parameter("workspace_max", [0.45, 0.45, 0.45])
        self.declare_parameter("max_ee_speed", 0.35)
        self.declare_parameter("margin", 0.05)
        limits = SafetyLimits(
            workspace_min=tuple(self.get_parameter("workspace_min").value),
            workspace_max=tuple(self.get_parameter("workspace_max").value),
            max_ee_speed=float(self.get_parameter("max_ee_speed").value),
            margin=float(self.get_parameter("margin").value))
        self.supervisor = SafetySupervisor(limits)
        self.srv = self.create_service(CheckTrajectory, "/check_trajectory",
                                       self.cb_check)
        self.sub_traj = self.create_subscription(
            ObjectArray(), "/planned_trajectory", self.cb_planned, 10)
        self.pub_verdict = self.create_publisher(SafetyVerdict,
                                                 "/safety/verdict", 10)
        self.scene = []      # [(name, pos, radius)] maintained externally
        self.get_logger().info("safety_supervisor ready (independent process)")

    def cb_check(self, request, response):
        traj = json.loads(request.trajectory_json)
        waypoints = [(wp["t"], wp["pos"]) for wp in traj.get("waypoints", [])]
        dt = float(traj.get("dt", 0.5))
        objects = [(o[0], np.asarray(o[1], float), float(o[2]))
                   for o in traj.get("objects", self.scene)]
        verdict = self.supervisor.check_trajectory(
            waypoints, dt=dt, objects=objects,
            held=request.held_object or None)
        response.verdict.accepted = verdict.accepted
        response.verdict.reason = verdict.reason
        response.verdict.violated = verdict.violated
        self.pub_verdict.publish(response.verdict)
        return response

    def cb_planned(self, msg):
        self.pub_verdict.publish(SafetyVerdict(accepted=False,
                                               reason="no cached scene check"))


def ObjectArray():  # placeholder import target replaced at build time
    from sensor_msgs.msg import JointState  # noqa: F401
    from rg_msgs.msg import ObjectEstimateArray
    return ObjectEstimateArray


def main(args=None):
    rclpy.init(args=args)
    node = SafetySupervisorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
