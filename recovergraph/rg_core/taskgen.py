"""Task-suite generator (spec 8): horizons 1..10 subgoals, 20 templates per
horizon, randomized object choices. Deterministic given (horizon, template_id).
"""
from __future__ import annotations

from .task_graph import Subgoal, TaskGraph
from .types import holding_pred, in_pred, on_pred

CUBES = ["red_cube", "blue_cube", "green_cube", "yellow_cube"]
REGIONS = ["bin", "plate"]
NICE = {"red_cube": "red cube", "blue_cube": "blue cube",
        "green_cube": "green cube", "yellow_cube": "yellow cube",
        "bin": "the bin", "plate": "the plate"}


def make_task(horizon: int, template_id: int = 0, seed: int = 0) -> TaskGraph:
    """Build a task with exactly `horizon` subgoals.

    Operation patterns cycle with template_id so each horizon has 20 distinct
    templates (spec 8): pick+place-to-region, pick+stack-on-cube, ee move.
    Dependencies form a chain; each edge carries the predecessor's effect
    variables. Previously placed objects become invariants.
    """
    if not 1 <= horizon <= 10:
        raise ValueError("horizon must be in 1..10 (spec 8)")
    subgoals: list = []
    edges: list = []
    invariants: list = []
    goal_parts: list = []
    prev_id = None
    gid = 1
    last_placed = None          # (obj, subgoal_id) of last stack base
    ci = template_id
    placed_or_base = set()      # objects whose invariants a later pick would break
    while len(subgoals) < horizon:
        mode = (ci + gid) % 3
        remaining = horizon - len(subgoals)
        available = [c for c in CUBES if c not in placed_or_base]
        if mode in (0, 1) and not available:
            mode = 2            # nothing free to pick: emit a free-space move
        if mode == 0:                       # pick, transport, place into region
            obj = available[ci % len(available)]
            region = REGIONS[ci % len(REGIONS)]
            g_pick = Subgoal(f"g{gid}", "pick", {"obj": obj},
                             ["reachable(obj)", "gripper_empty"],
                             [holding_pred(obj)])
            gid += 1
            if remaining >= 3:
                g_move = Subgoal(f"g{gid}", "move",
                                 {"target": region, "obj": obj},
                                 [holding_pred(obj)],
                                 [f"ee_at({region})"])
                gid += 1
                g_place = Subgoal(f"g{gid}", "place",
                                  {"obj": obj, "target": region},
                                  [holding_pred(obj)],
                                  [in_pred(obj, region), "gripper_empty"])
                gid += 1
                edges.append((g_pick.id, g_move.id, [holding_pred(obj)]))
                edges.append((g_move.id, g_place.id, [holding_pred(obj)]))
                subgoals += [g_pick, g_move, g_place]
                if prev_id and prev_id != g_pick.id:
                    edges.append((prev_id, g_pick.id, _carried(subgoals, prev_id)))
                prev_id = g_place.id
                invariants.append(f"{in_pred(obj, region)} must hold until end")
                placed_or_base.add(obj)
                goal_parts.append(f"put {NICE[obj]} in {NICE[region]}")
            elif remaining == 2:
                g_move = Subgoal(f"g{gid}", "move",
                                 {"target": region, "obj": obj},
                                 [holding_pred(obj)],
                                 [f"ee_at({region})"])
                gid += 1
                edges.append((g_pick.id, g_move.id, [holding_pred(obj)]))
                subgoals += [g_pick, g_move]
                if prev_id and prev_id != g_pick.id:
                    edges.append((prev_id, g_pick.id, _carried(subgoals, prev_id)))
                prev_id = g_move.id
                goal_parts.append(f"pick up {NICE[obj]} and move above {NICE[region]}")
            else:
                subgoals.append(g_pick)
                if prev_id:
                    edges.append((prev_id, g_pick.id, _carried(subgoals, prev_id)))
                prev_id = g_pick.id
                goal_parts.append(f"pick up {NICE[obj]}")
        elif mode == 1:                     # pick & stack on another cube
            a = available[ci % len(available)]
            bases = [c for c in CUBES
                     if c != a and c not in placed_or_base]
            if not bases:
                mode = 2
            else:
                b = bases[ci % len(bases)]
                g_pick = Subgoal(f"g{gid}", "pick", {"obj": a},
                                 ["reachable(obj)", "gripper_empty"],
                                 [holding_pred(a)])
                gid += 1
                if remaining >= 2:
                    pre = [holding_pred(a)]
                    g_place = Subgoal(f"g{gid}", "place",
                                      {"obj": a, "target": f"on({b})"},
                                      pre, [on_pred(a, b), "gripper_empty"])
                    gid += 1
                    edges.append((g_pick.id, g_place.id, [holding_pred(a)]))
                    subgoals += [g_pick, g_place]
                    if prev_id and prev_id != g_pick.id:
                        edges.append((prev_id, g_pick.id,
                                      _carried(subgoals, prev_id)))
                    prev_id = g_place.id
                    last_placed = (a, g_place.id)
                    invariants.append(f"{on_pred(a, b)} must hold until end")
                    placed_or_base.update((a, b))
                    goal_parts.append(f"stack {NICE[a]} on {NICE[b]}")
                else:
                    subgoals.append(g_pick)
                    if prev_id:
                        edges.append((prev_id, g_pick.id,
                                      _carried(subgoals, prev_id)))
                    prev_id = g_pick.id
                    goal_parts.append(f"pick up {NICE[a]}")
        if mode == 2:                       # free-space move
            region = REGIONS[ci % len(REGIONS)]
            g_move = Subgoal(f"g{gid}", "move", {"target": region}, [],
                             [f"ee_at({region})"])
            gid += 1
            subgoals.append(g_move)
            if prev_id:
                edges.append((prev_id, g_move.id, _carried(subgoals, prev_id)))
            prev_id = g_move.id
            goal_parts.append(f"move above {NICE[region]}")
        ci += 1
    goal = ", then ".join(goal_parts)
    return TaskGraph(f"t_{horizon:02d}_{template_id:02d}", goal, subgoals,
                     edges, invariants)


def _carried(subgoals: list, sid: str) -> list:
    for s in subgoals:
        if s.id == sid:
            return list(s.eff)
    return []


def objects_for_template(template_id: int) -> list:
    """Objects possibly used by a template (for scene generation)."""
    base = list(CUBES)
    base.append("cyl_1")
    return base
