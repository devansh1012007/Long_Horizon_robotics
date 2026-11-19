"""task_planner node (spec Section 7): builds/repairs the task graph.

Publishes /task/graph; /replan takes a stale set + corrupted vars and returns
a patched graph using rg_core.task_graph.mark_stale (spec 6.5).
"""
import json

import rclpy
from rclpy.node import Node

from rg_msgs.msg import TaskGraphMsg
from rg_msgs.srv import Replan
from rg_core.task_graph import TaskGraph


class TaskPlannerNode(Node):

    def __init__(self):
        super().__init__("task_planner")
        self.sub_goal = self.create_subscription(
            TaskGraphMsg, "/task/goal", self.cb_goal, 10)
        self.pub_graph = self.create_publisher(TaskGraphMsg, "/task/graph", 10)
        self.srv_replan = self.create_service(Replan, "/replan", self.cb_replan)
        self.current: TaskGraph | None = None
        self.get_logger().info("task_planner ready")

    def cb_goal(self, msg: TaskGraphMsg):
        try:
            self.current = TaskGraph.from_json(msg.graph_json)
        except ValueError as exc:
            self.get_logger().error(f"invalid task graph: {exc}")
            return
        out = TaskGraphMsg()
        out.task_id = self.current.task_id
        out.goal = self.current.goal
        out.graph_json = self.current.to_json()
        self.pub_graph.publish(out)
        self.get_logger().info(f"published /task/graph {self.current.task_id}")

    def cb_replan(self, request, response):
        if self.current is None:
            response.success = False
            response.error = "no task graph loaded"
            return response
        stale, corrupted = self.current.mark_stale(list(request.corrupted_vars))
        stale.update(request.stale_subgoals)
        # patch: annotate stale subgoals by re-serialising with a stale set in
        # the graph payload (executor re-verifies stale preconditions, 6.5)
        graph = json.loads(self.current.to_json())
        graph["stale"] = sorted(stale)
        out = TaskGraphMsg()
        out.task_id = self.current.task_id
        out.goal = self.current.goal
        out.graph_json = json.dumps(graph)
        response.patched_graph = out
        response.success = True
        response.error = ""
        return response


def main(args=None):
    rclpy.init(args=args)
    node = TaskPlannerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
