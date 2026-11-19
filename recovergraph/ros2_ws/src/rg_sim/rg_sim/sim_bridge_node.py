"""sim_bridge node (spec Section 7 + 8): owns the world and executes skills.

M4 bring-up: the world is created through rgsim.backends, so the bridge runs
the kinematic backend (CI) or the MuJoCo physics backend (ADR-001) behind
the same service contract. Publishes /rg_sim/truth (with camera-visible
flags), /joint_states and /gripper/state; serves /rg_sim/execute_skill with
fault injection carried in params_json. Replaced by MoveIt 2 + ros2_control
on hardware (spec Section 12).
"""
import json

import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState

from rg_msgs.msg import GripperState, ObjectEstimate, ObjectEstimateArray
from rg_msgs.srv import ExecuteSkill
from rgsim.backends import get_skills, make_world


class SimBridgeNode(Node):

    def __init__(self, seed: int = 0, cubes: list | None = None,
                 backend: str = "kinematic"):
        super().__init__("sim_bridge")
        cubes = cubes or ["red_cube", "blue_cube", "green_cube", "yellow_cube"]
        self.declare_parameter("seed", int(seed))
        self.declare_parameter("cubes", list(cubes))
        self.declare_parameter("backend", str(backend))
        seed = int(self.get_parameter("seed").value)
        self.backend = str(self.get_parameter("backend").value)
        self.skills = get_skills(self.backend)
        self.rng = np.random.default_rng(seed + 1)
        self.world = make_world(seed, list(self.get_parameter("cubes").value),
                                backend=self.backend)
        self.pub_truth = self.create_publisher(ObjectEstimateArray,
                                               "/rg_sim/truth", 10)
        self.pub_joint = self.create_publisher(JointState, "/joint_states", 10)
        self.pub_gripper = self.create_publisher(GripperState,
                                                 "/gripper/state", 10)
        self.srv = self.create_service(ExecuteSkill, "/rg_sim/execute_skill",
                                       self.cb_execute)
        self.get_logger().info(
            f"sim_bridge ready (backend={self.backend})")

    # ------------------------------------------------------------------ #
    def publish_world(self, occluded: set | None = None) -> None:
        """One camera frame of the world: truth with visibility flags (the
        occlusion mask is a camera property, so it is applied here), joint
        state and gripper state."""
        occluded = occluded or set()
        msg = ObjectEstimateArray()
        msg.header.stamp = self.get_clock().now().to_msg()
        for name, o in self.world.objects.items():
            est = ObjectEstimate()
            est.name = name
            est.pose_mean = [float(x) for x in o.pose]
            est.pose_cov_diag = [0.0] * 4
            est.identity_conf = 1.0
            est.visible = name not in occluded
            msg.objects.append(est)
        self.pub_truth.publish(msg)

        js = JointState()
        js.header.stamp = self.get_clock().now().to_msg()
        js.name = ["bridge_ee_x", "bridge_ee_y", "bridge_ee_z", "bridge_ee_yaw"]
        js.position = [float(x) for x in self.world.ee]
        self.pub_joint.publish(js)

        g = GripperState()
        g.header.stamp = self.get_clock().now().to_msg()
        g.state = self.world.gripper.state
        g.width = self.world.gripper.width
        g.force = self.world.gripper.force
        g.commanded_force = self.world.gripper.commanded_force
        self.pub_gripper.publish(g)

    # kept for API compatibility with the milestone-1 scaffold (timer tick)
    def cb_tick(self):
        self.publish_world()

    def cb_execute(self, request, response):
        args = json.loads(request.args_json or "{}")
        params = json.loads(request.params_json or "{}")
        fault = None
        if params.get("fault"):
            fault = {"fault": params["fault"],
                     "params": dict(params.get("fault_params", {}))}
        skill = request.skill
        p_grasp = float(params.get("p_grasp", 0.98))
        p_slip = float(params.get("p_slip", 0.008))
        if skill == "pick":
            res = self.skills["pick"](self.world, args["obj"], self.rng,
                                       p_grasp, fault)
        elif skill == "move":
            res = self.skills["move"](self.world,
                                      np.asarray(args["target"], float),
                                      float(args.get("yaw", 0.0)), self.rng,
                                      p_slip, fault)
        elif skill == "place":
            res = self.skills["place"](self.world,
                                       np.asarray(args["target"], float),
                                       float(args.get("yaw", 0.0)), self.rng,
                                       fault)
        else:
            response.success = False
            response.status = f"unknown skill {skill}"
            response.telemetry_json = "{}"
            return response
        response.success = res.status == "ok"
        response.status = res.status
        response.telemetry_json = json.dumps(res.telemetry)
        return response


def main(args=None):
    rclpy.init(args=args)
    node = SimBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
