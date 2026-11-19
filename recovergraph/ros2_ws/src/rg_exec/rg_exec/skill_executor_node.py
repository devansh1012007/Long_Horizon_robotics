"""skill_executor node (spec Section 7): action server /skill/execute.

M4 bring-up: before every motion the executor sends the actual planned
trajectory (waypoints + scene objects + held object, carried in params_json)
to the independent safety supervisor (/check_trajectory); vetoed motions are
never executed. Accepted motions are delegated to the environment bridge
(/rg_sim/execute_skill) with the skill parameters and fault hooks; the
telemetry comes back over the service and is returned to the caller.
"""
import json

import rclpy
from rclpy.action import ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

from rg_msgs.action import SkillExecute
from rg_msgs.msg import ExecEvent, SafetyVerdict
from rg_msgs.srv import CheckTrajectory, ExecuteSkill


class SkillExecutorNode(Node):

    def __init__(self):
        super().__init__("skill_executor")
        self.cb_group = ReentrantCallbackGroup()
        self.action_server = ActionServer(
            self, SkillExecute, "/skill/execute", self.cb_execute,
            callback_group=self.cb_group)
        self.pub_events = self.create_publisher(ExecEvent, "/exec/events", 10)
        self.cli_check = self.create_client(CheckTrajectory,
                                            "/check_trajectory")
        self.cli_sim = self.create_client(ExecuteSkill,
                                          "/rg_sim/execute_skill")
        self.get_logger().info("skill_executor ready on /skill/execute")

    def cb_execute(self, goal_handle):
        req = goal_handle.request
        params = json.loads(req.params_json or "{}")
        safety = params.get("safety", {})
        check = CheckTrajectory.Request()
        check.trajectory_json = json.dumps(safety)
        check.held_object = safety.get("held", "") or ""
        future = self.cli_check.call_async(check)
        rclpy.spin_until_future_complete(self, future, timeout_sec=2.0)
        verdict = future.result().verdict if future.result() else \
            SafetyVerdict(accepted=False, reason="supervisor unreachable")
        if not verdict.accepted:
            # spec 6.8: vetoed motions are never executed
            self._event(req.episode_id, "safety_supervisor", "veto",
                        verdict.reason)
            goal_handle.abort()
            result = SkillExecute.Result()
            result.success = False
            result.status = "vetoed"
            result.telemetry_json = json.dumps(
                {"status": "vetoed", "violated": verdict.violated})
            return result

        self._event(req.episode_id, "skill_executor", "exec_start",
                    {"skill": req.skill})
        # delegate execution to the environment bridge (sim now, MoveIt 2 +
        # ros2_control on hardware)
        sim_req = ExecuteSkill.Request()
        sim_req.episode_id = req.episode_id
        sim_req.skill = req.skill
        sim_req.args_json = req.args_json or "{}"
        sim_req.params_json = json.dumps(params.get("skill", {}))
        sim_future = self.cli_sim.call_async(sim_req)
        rclpy.spin_until_future_complete(self, sim_future, timeout_sec=60.0)
        sim_res = sim_future.result()
        goal_handle.succeed()
        result = SkillExecute.Result()
        result.success = bool(sim_res.success)
        result.status = sim_res.status
        result.telemetry_json = sim_res.telemetry_json
        result.effects_json = req.args_json or "{}"
        self._event(req.episode_id, "skill_executor", "exec_done",
                    {"skill": req.skill, "status": sim_res.status})
        return result

    def _event(self, episode_id, node, etype, payload):
        msg = ExecEvent()
        msg.episode_id = episode_id
        now = self.get_clock().now().to_msg()
        msg.t = now.sec + now.nanosec * 1e-9
        msg.node = node
        msg.type = etype
        msg.payload_json = json.dumps(payload)
        self.pub_events.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = SkillExecutorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
