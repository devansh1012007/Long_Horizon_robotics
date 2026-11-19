"""LADDER-aligned task generator (docs/LADDER_ADAPTER.md, structural adoption).

Task families mirror LADDER's RoboLab-Long structure (TASKS.md): object-
destination subgoals, 1-10 per task, with routing / relay / revisit / ordered
semantics. Subgoal counting follows LADDER (one object-destination = one
subgoal); the internal skill-level decomposition (pick/move/place) is the
pre-registered horizon metric and is unchanged.

Families:
  comp_bin       put N objects in the bin
  comp_route     split objects between plate and bin
  comp_relay     plate -> bin chains (same object moved twice)
  comp_revisit   bin -> plate -> bin revisits
  comp_ordered   order-constrained bin fills

Deterministic given (family, n_subgoals, seed).
"""
from __future__ import annotations

from .task_graph import Subgoal, TaskGraph
from .types import holding_pred, in_pred

CUBES = ["red_cube", "blue_cube", "green_cube", "yellow_cube"]
REGIONS = ["bin", "plate"]


def _transport(gid, obj, dest, subgoals, edges, prev, invariants,
                label=None):
    """One object-destination: pick -> move -> place (skill-level chain)."""
    g_pick = Subgoal(f"g{gid}", "pick", {"obj": obj},
                     ["reachable(obj)", "gripper_empty"],
                     [holding_pred(obj)])
    g_move = Subgoal(f"g{gid + 1}", "move", {"target": dest, "obj": obj},
                     [holding_pred(obj)], [f"ee_at({dest})"])
    g_place = Subgoal(f"g{gid + 2}", "place", {"obj": obj, "target": dest},
                      [holding_pred(obj)],
                      [in_pred(obj, dest), "gripper_empty"])
    subgoals += [g_pick, g_move, g_place]
    edges.append((g_pick.id, g_move.id, [holding_pred(obj)]))
    edges.append((g_move.id, g_place.id, [holding_pred(obj)]))
    if prev:
        edges.append((prev, g_pick.id, []))
    # only the object's FINAL destination carries the 'until end' invariant
    # (intermediate placements in relay/revisit families and re-transported
    # objects are deliberate moves, not invariant breaks)
    invariants[:] = [iv for iv in invariants if f"({obj}," not in iv]
    invariants.append(f"{in_pred(obj, dest)} must hold until end")
    return g_place.id, gid + 3


def make_ladder_task(family: str, n_subgoals: int, seed: int = 0) -> TaskGraph:
    if not 1 <= n_subgoals <= 10:
        raise ValueError("LADDER subgoal count is 1..10")
    import numpy as np
    rng = np.random.default_rng(seed)
    subgoals, edges, invariants = [], [], []
    prev = None
    gid = 1
    goal_parts = []

    def obj_for(i):
        return CUBES[(seed + i) % len(CUBES)]

    if family == "comp_bin":
        for i in range(n_subgoals):
            obj = obj_for(i)
            prev, gid = _transport(gid, obj, "bin", subgoals, edges, prev,
                                   invariants)
            goal_parts.append(f"put {obj} in the bin")
    elif family == "comp_route":
        for i in range(n_subgoals):
            obj = obj_for(i)
            dest = REGIONS[int(rng.integers(0, 2))]
            prev, gid = _transport(gid, obj, dest, subgoals, edges, prev,
                                   invariants)
            goal_parts.append(f"put {obj} on the {dest}")
    elif family == "comp_relay":
        # plate -> bin chains: each object goes plate first, then bin
        n_pairs = max(1, n_subgoals // 2)
        for i in range(n_pairs):
            obj = obj_for(i)
            prev, gid = _transport(gid, obj, "plate", subgoals, edges, prev,
                                   invariants)
            prev, gid = _transport(gid, obj, "bin", subgoals, edges, prev,
                                   invariants)
            goal_parts.append(f"relay {obj} plate->bin")
    elif family == "comp_revisit":
        # bin -> plate -> bin for one object per triple
        n_obj = max(1, n_subgoals // 3)
        for i in range(n_obj):
            obj = obj_for(i)
            prev, gid = _transport(gid, obj, "bin", subgoals, edges, prev,
                                   invariants)
            prev, gid = _transport(gid, obj, "plate", subgoals, edges, prev,
                                   invariants)
            prev, gid = _transport(gid, obj, "bin", subgoals, edges, prev,
                                   invariants)
            goal_parts.append(f"revisit {obj}")
    elif family == "comp_ordered":
        for i in range(n_subgoals):
            obj = obj_for(i)
            prev, gid = _transport(gid, obj, "bin", subgoals, edges, prev,
                                   invariants)
            goal_parts.append(f"first {obj}, then" if i == 0 else
                              (f" then {obj}" if i else f" {obj}"))
    else:
        raise ValueError(f"unknown family {family}")
    goal = ", then ".join(goal_parts[:8]) + \
        (" ..." if len(goal_parts) > 8 else "")
    return TaskGraph(f"ladder_{family}_{n_subgoals:02d}_{seed:03d}",
                     goal, subgoals, edges, invariants)


FAMILIES = ["comp_bin", "comp_route", "comp_relay", "comp_revisit",
            "comp_ordered"]
