"""state_estimator node (spec Section 7): /scene/objects + /joint_states +
/gripper/state -> /belief/state (spec 5.3).

M4 bring-up: assembles the full belief (per-object estimates with cov, EE
pose with telemetry noise, noisy gripper state) and publishes it both as
the typed message and as the full serialised belief (belief_json), which
the episode driver consumes.
"""
import json

import numpy as np
import rclpy
from rclpy.node import Node

from rg_msgs.msg import BeliefState, ObjectEstimateArray
from rg_msgs.msg import GripperState as GripperStateMsg
from sensor_msgs.msg import JointState as JointStateMsg
from rg_core.types import BeliefState as CoreBelief
from rg_core.types import GripperState, ObjectEst
from rgsim.noise import NoiseCfg


class StateEstimatorNode(Node):

    def __init__(self, seed: int = 0, ee_sigma: float = NoiseCfg.ee_sigma):
        super().__init__("state_estimator")
        self.declare_parameter("seed", int(seed))
        self.declare_parameter("ee_sigma", float(ee_sigma))
        self.ee_sigma = float(self.get_parameter("ee_sigma").value)
        self.rng = np.random.default_rng(
            int(self.get_parameter("seed").value))
        self.sub_scene = self.create_subscription(
            ObjectEstimateArray, "/scene/objects", self.cb_scene, 10)
        self.sub_joint = self.create_subscription(
            JointStateMsg, "/joint_states", self.cb_joint, 10)
        self.sub_grip = self.create_subscription(
            GripperStateMsg, "/gripper/state", self.cb_grip, 10)
        self.pub_belief = self.create_publisher(BeliefState, "/belief/state",
                                                10)
        self.belief = CoreBelief(
            t=0.0,
            ee_pose=np.array([0.0, -0.35, 0.12, 0.0]),
            ee_cov_diag=np.array([self.ee_sigma**2] * 3 +
                                 [(2 * self.ee_sigma)**2]),
            gripper=GripperState(),
            objects={}, confidence={})
        self._ee = np.array([0.0, -0.35, 0.12, 0.0])
        self._grip = GripperState()
        self._scene: ObjectEstimateArray | None = None
        self.get_logger().info("state_estimator ready")

    # ------------------------------------------------------------------ #
    def cb_scene(self, msg: ObjectEstimateArray):
        self._scene = msg
        for est in msg.objects:
            self.belief.objects[est.name] = ObjectEst(
                est.name, np.asarray(est.pose_mean, float),
                np.asarray(est.pose_cov_diag, float),
                est.identity_conf, est.visible)
            self.belief.confidence[f"visible({est.name})"] = \
                1.0 if est.visible else 0.0
        self.publish()

    def cb_joint(self, msg):
        pos = list(msg.position)
        self._ee = np.array(pos[:4] if len(pos) >= 4 else
                            [pos[0], pos[1], pos[2], 0.0], float)
        self.belief.t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self.publish()

    def cb_grip(self, msg):
        self._grip = GripperState(msg.state, msg.width, msg.force,
                                  msg.commanded_force)
        self.publish()

    # ------------------------------------------------------------------ #
    def publish(self):
        ee_mean = self._ee + self.rng.normal(0.0, self.ee_sigma, 4)
        self.belief.ee_pose = ee_mean
        self.belief.ee_cov_diag = np.array([self.ee_sigma**2] * 3 +
                                           [(2 * self.ee_sigma)**2])
        g = self._grip
        self.belief.gripper = GripperState(
            g.state,
            max(0.0, g.width + float(self.rng.normal(0, 0.002))),
            max(0.0, g.force + float(self.rng.normal(0, 0.5))),
            g.commanded_force)
        if self._scene is not None:
            self.belief.t = (self._scene.header.stamp.sec +
                             self._scene.header.stamp.nanosec * 1e-9)
        out = BeliefState()
        out.header.stamp = self.get_clock().now().to_msg()
        out.ee_pose = [float(x) for x in self.belief.ee_pose]
        out.ee_cov_diag = [float(x) for x in self.belief.ee_cov_diag]
        out.gripper_state = self.belief.gripper.state
        out.gripper_width = self.belief.gripper.width
        out.gripper_force = self.belief.gripper.force
        out.gripper_commanded_force = self.belief.gripper.commanded_force
        out.objects = [_to_msg_est(o) for o in self.belief.objects.values()]
        out.confidence_json = json.dumps(self.belief.confidence)
        out.belief_json = self.belief.to_json()
        self.pub_belief.publish(out)


def _to_msg_est(o: ObjectEst):
    from rg_msgs.msg import ObjectEstimate
    m = ObjectEstimate()
    m.name = o.name
    m.pose_mean = [float(x) for x in o.pose]
    m.pose_cov_diag = [float(x) for x in o.cov_diag]
    m.identity_conf = o.identity_conf
    m.visible = o.visible
    return m


def main(args=None):
    rclpy.init(args=args)
    node = StateEstimatorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
