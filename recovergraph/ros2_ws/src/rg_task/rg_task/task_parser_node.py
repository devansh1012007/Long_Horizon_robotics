"""task_parser node (spec Section 7): NL instruction -> task graph.

LLM-free v1 (spec Section 1 out-of-scope rule: LLMs may only parse language
into a task graph): a deterministic rule-based parser over templated goals
("put X in Y", "stack X on Y", "move above Y", chained with ", then").
"""
import json
import re

import rclpy
from rclpy.node import Node

from rg_msgs.msg import TaskGraphMsg, TaskGoal
from rg_msgs.srv import ParseInstruction

CUBES = ["red_cube", "blue_cube", "green_cube", "yellow_cube"]
REGIONS = ["bin", "plate"]
NICE = {"red cube": "red_cube", "blue cube": "blue_cube",
        "green cube": "green_cube", "yellow cube": "yellow_cube",
        "the bin": "bin", "bin": "bin", "the plate": "plate",
        "plate": "plate"}


def _canon(name: str):
    return NICE.get(name.strip().lower())


class TaskParserNode(Node):

    def __init__(self):
        super().__init__("task_parser")
        self.srv = self.create_service(
            ParseInstruction, "/parse_instruction", self.cb_parse)
        self.pub_goal = self.create_publisher(TaskGoal, "/task/goal", 10)
        self.get_logger().info("task_parser ready on /parse_instruction")

    def cb_parse(self, request, response):
        graph = self.parse(request.instruction)
        if graph is None:
            response.error = ("instruction not parseable by the rule-based "
                              "parser; use 'put X in Y, then stack A on B'")
            return response
        response.graph.task_id = graph["task_id"]
        response.graph.goal = graph["goal"]
        response.graph.graph_json = json.dumps(graph["graph"])
        response.error = ""
        goal = TaskGoal()
        goal.goal_text = request.instruction
        goal.episode_id = ""
        self.pub_goal.publish(goal)
        return response

    def parse(self, instruction: str):
        subs, edges, invariants = [], [], []
        prev = None
        gid = 0
        placed_or_base = set()
        parts = [p.strip() for p in instruction.lower().split(", then")]
        for part in parts:
            m = re.match(r"put (.+?) in (.+)", part)
            kind = None
            if m:
                obj, region = _canon(m.group(1)), _canon(m.group(2))
                if obj not in CUBES or region not in REGIONS:
                    return None
                if obj in placed_or_base:
                    return None
                gid += 1
                gp = f"g{gid}"
                subs.append({"id": gp, "skill": "pick",
                             "args": {"obj": obj},
                             "pre": ["reachable(obj)", "gripper_empty"],
                             "eff": [f"holding({obj})"]})
                prev_edge = (prev, gp, [f"holding({obj})"]) if prev else None
                gid += 1
                gm = f"g{gid}"
                subs.append({"id": gm, "skill": "move",
                             "args": {"target": region, "obj": obj},
                             "pre": [f"holding({obj})"],
                             "eff": [f"ee_at({region})"]})
                gid += 1
                gpl = f"g{gid}"
                subs.append({"id": gpl, "skill": "place",
                             "args": {"obj": obj, "target": region},
                             "pre": [f"holding({obj})"],
                             "eff": [f"in({obj},{region})", "gripper_empty"]})
                edges.append((gp, gm, [f"holding({obj})"]))
                edges.append((gm, gpl, [f"holding({obj})"]))
                if prev_edge:
                    edges.append(prev_edge)
                invariants.append(f"in({obj},{region}) must hold until end")
                placed_or_base.add(obj)
                prev = gpl
                kind = "ok"
                continue
            m = re.match(r"stack (.+?) on (.+)", part)
            if m:
                a, b = _canon(m.group(1)), _canon(m.group(2))
                if a not in CUBES or b not in CUBES or a == b \
                        or a in placed_or_base or b in placed_or_base:
                    return None
                gid += 1
                gp = f"g{gid}"
                subs.append({"id": gp, "skill": "pick", "args": {"obj": a},
                             "pre": ["reachable(obj)", "gripper_empty"],
                             "eff": [f"holding({a})"]})
                gid += 1
                gpl = f"g{gid}"
                subs.append({"id": gpl, "skill": "place",
                             "args": {"obj": a, "target": f"on({b})"},
                             "pre": [f"holding({a})"],
                             "eff": [f"on({a},{b})", "gripper_empty"]})
                edges.append((gp, gpl, [f"holding({a})"]))
                if prev:
                    edges.append((prev, gp, [f"gripper_empty"]))
                invariants.append(f"on({a},{b}) must hold until end")
                placed_or_base.update((a, b))
                prev = gpl
                kind = "ok"
                continue
            m = re.match(r"move above (.+)", part)
            if m:
                region = _canon(m.group(1))
                if region not in REGIONS:
                    return None
                gid += 1
                gm = f"g{gid}"
                subs.append({"id": gm, "skill": "move",
                             "args": {"target": region},
                             "pre": [], "eff": [f"ee_at({region})"]})
                if prev:
                    edges.append((prev, gm, [f"ee_at({region})"]))
                prev = gm
                kind = "ok"
                continue
            return None
        if not subs:
            return None
        task_id = f"t_nl_{len(subs)}"
        return {"task_id": task_id, "goal": instruction,
                "graph": {"task_id": task_id, "goal": instruction,
                          "subgoals": subs,
                          "depends_on": [list(e) for e in edges],
                          "invariants": invariants}}


def main(args=None):
    rclpy.init(args=args)
    node = TaskParserNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
