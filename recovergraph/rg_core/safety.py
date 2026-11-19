"""Independent safety supervisor logic (spec 6.8).

Pure decision logic; the ROS node (rg_exec/safety_supervisor_node.py) runs
this in its own process. Its constraints are never traded against cost: it
only sees the trajectory and the scene, and returns ACCEPT/REJECT with a
reason. Every decision is logged and vetoed attempts are counted.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .types import SafetyVerdict


@dataclass
class SafetyLimits:
    workspace_min: tuple = (-0.45, -0.45, 0.005)   # metres, table top at z=0
    workspace_max: tuple = (0.45, 0.45, 0.45)
    max_ee_speed: float = 0.35      # m/s between waypoints
    margin: float = 0.05            # inflated collision margin (spec 6.8)
    ee_radius: float = 0.06


class SafetySupervisor:
    def __init__(self, limits: SafetyLimits | None = None):
        self.limits = limits or SafetyLimits()

    def check_trajectory(self, waypoints: list, dt: float, objects: list,
                         held: str | None = None) -> SafetyVerdict:
        """waypoints: iterable of (t, pos(3,)) sampled along the path.
        objects: iterable of (name, pos(3,), radius) known to the supervisor.
        held: name of the object currently in the gripper (excluded from
        collision checks since it is attached to the EE)."""
        lim = self.limits
        violated = []
        prev_t, prev_p = None, None
        for wp in waypoints:
            t, p = float(wp[0]), np.asarray(wp[1], float)
            if not np.all(p >= np.array(lim.workspace_min)) or \
               not np.all(p <= np.array(lim.workspace_max)):
                violated.append(f"workspace_bounds@t={t:.2f}")
                return SafetyVerdict(False, "trajectory leaves workspace bounds",
                                     violated)
            if prev_p is not None and dt > 0:
                speed = float(np.linalg.norm(p - prev_p) / (t - prev_t))
                if speed > lim.max_ee_speed:
                    violated.append(f"speed_limit@t={t:.2f} ({speed:.2f} m/s)")
                    return SafetyVerdict(False, "EE speed above limit", violated)
            for name, opos, orad in objects:
                if name == held:
                    continue
                d = float(np.linalg.norm(p - np.asarray(opos, float)))
                if d < lim.ee_radius + orad + lim.margin:
                    violated.append(
                        f"collision_margin@t={t:.2f} vs {name} (d={d:.3f}m)")
                    return SafetyVerdict(False,
                                         f"collision margin violated near {name}",
                                         violated)
            prev_t, prev_p = t, p
        return SafetyVerdict(True, "ok", [])
