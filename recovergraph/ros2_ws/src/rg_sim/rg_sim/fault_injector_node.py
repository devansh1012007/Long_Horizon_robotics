"""fault_injector node (spec 10.1, ROS side): holds a seeded fault schedule
and exposes it to the bridge / orchestrator via /rg_sim/fault_schedule."""
import json

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from rgsim.fault_injector import episode_schedule


class FaultInjectorNode(Node):

    def __init__(self):
        super().__init__("fault_injector")
        self.declare_parameter("seed", 42)
        self.declare_parameter("condition", "single")
        self.declare_parameter("horizon", 3)
        self.declare_parameter("stratify", "random")
        seed = int(self.get_parameter("seed").value)
        horizon = int(self.get_parameter("horizon").value)
        schedule = episode_schedule(
            seed, horizon,
            self.get_parameter("condition").value,
            self.get_parameter("stratify").value)
        self.pub = self.create_publisher(String, "/rg_sim/fault_schedule", 10)
        self.timer = self.create_timer(
            0.5, lambda: self.pub.publish(String(
                data=json.dumps([e.to_dict() for e in schedule]))))
        self.get_logger().info(
            f"fault_injector ready: {len(schedule)} fault(s), seed={seed}")

    def schedule(self):
        return [e.to_dict() for e in episode_schedule(
            int(self.get_parameter("seed").value),
            int(self.get_parameter("horizon").value),
            self.get_parameter("condition").value)]


def main(args=None):
    rclpy.init(args=args)
    node = FaultInjectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
