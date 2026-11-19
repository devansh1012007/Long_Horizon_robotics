"""Kinematic tabletop world (spec 8, milestone-1 simulator).

Pure NumPy kinematics: objects rest on a table, the end-effector is a point
with a parallel gripper; grasped objects follow the EE. Contact physics is
reduced to probabilistic outcomes (grasp success, slip, placement error)
sampled from seeded RNGs, with the 7 spec faults injected by mechanism.
Replaced by MuJoCo in Phase 0/1 per ADR-001.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from rg_core.types import GripperState

TABLE_Z = 0.0
CUBE_HALF = 0.025          # 5 cm cubes
CUBE_TOP = 0.05
HOME = np.array([0.0, -0.35, 0.12, 0.0])


@dataclass
class SimObject:
    name: str
    kind: str                    # cube | cylinder | bin | plate
    half: float                  # half-extent (cube) or radius
    height: float
    pose: np.ndarray             # (4,) x,y,z,yaw ground truth
    is_region: bool = False

    @property
    def radius(self) -> float:
        return self.half


@dataclass
class World:
    rng: np.random.Generator
    objects: dict = field(default_factory=dict)     # name -> SimObject
    ee: np.ndarray = field(default_factory=lambda: HOME.copy())
    gripper: GripperState = field(default_factory=GripperState)
    held: str | None = None
    grasp_offset: np.ndarray = field(default_factory=lambda: np.zeros(3))
    sim_t: float = 0.0

    # ------------------------------------------------------------------ #
    def add_object(self, obj: SimObject) -> None:
        self.objects[obj.name] = obj

    def region(self, name: str) -> SimObject:
        return self.objects[name]

    def object_top(self, name: str) -> np.ndarray:
        o = self.objects[name]
        return np.array([o.pose[0], o.pose[1], o.pose[2] + o.height, 0.0])

    def move_ee(self, target: np.ndarray, speed: float = 0.2) -> list:
        """Kinematic travel along a straight line; returns waypoints (t, xyz).
        A held object follows the EE with its grasp offset. Target may be
        3-vector (keep current yaw) or 4-vector [x, y, z, yaw]."""
        start = self.ee.copy()
        tgt = np.asarray(target, float)
        yaw = float(tgt[3]) if tgt.size > 3 else float(start[3])
        tgt3 = tgt[:3]
        dist = float(np.linalg.norm(tgt3 - start[:3]))
        n = max(2, int(np.ceil(dist / 0.1)) + 1)
        wps = []
        for i in range(n):
            t = self.sim_t + i * 0.5
            p = start[:3] + (tgt3 - start[:3]) * (i / (n - 1))
            wps.append((t, p.copy()))
            if self.held is not None:
                o = self.objects[self.held]
                o.pose[:3] = p + self.grasp_offset
                o.pose[3] = start[3] + (yaw - start[3]) * (i / (n - 1))
        self.sim_t += (n - 1) * 0.5
        self.ee = np.array([tgt3[0], tgt3[1], tgt3[2], yaw])
        if self.held is not None:
            o = self.objects[self.held]
            o.pose[:3] = self.ee[:3] + self.grasp_offset
            o.pose[3] = yaw
        return wps

    def in_region(self, obj_name: str, region: str) -> bool:
        o, r = self.objects[obj_name], self.objects[region]
        return float(np.hypot(o.pose[0] - r.pose[0], o.pose[1] - r.pose[1])) <= r.half

    def on_top(self, a: str, b: str) -> bool:
        pa, pb = self.objects[a].pose, self.objects[b].pose
        return (float(np.hypot(pa[0] - pb[0], pa[1] - pb[1])) < 0.03
                and abs(pa[2] - (pb[2] + self.objects[b].height)) < 0.02)

    def displace(self, name: str, dist_range=(0.03, 0.15)) -> np.ndarray:
        """External disturbance (f_collision / stale_dependency): shift an
        object by a random horizontal offset; returns the applied shift."""
        ang = self.rng.uniform(0, 2 * np.pi)
        d = self.rng.uniform(*dist_range)
        shift = np.array([d * np.cos(ang), d * np.sin(ang), 0.0])
        o = self.objects[name]
        if self.held == name:
            self.held = None
            self.gripper = GripperState("lost", 0.08, 0.0, self.gripper.commanded_force)
        o.pose[:3] = np.clip(o.pose[:3] + shift, -0.42, 0.42)
        o.pose[2] = rest_z(o)
        return shift


def rest_z(o: SimObject) -> float:
    return 0.0 if o.is_region else (CUBE_HALF if o.kind == "cube" else o.height / 2)


def generate_scene(seed: int, cubes: list, with_cyl: bool = True) -> World:
    """Randomized placements (spec 8): 3-8 objects, one bin, one plate,
    AprilTags abstracted away (calibration is assumed perfect in sim).

    Placement keeps every object pair >= 0.20 m apart (outside the safety
    veto radius) with a best-effort max-min fallback so the sampler can
    never fall through to an overlapping position (which would make
    fault-free picks physically disturb neighbours on the physics backend).
    """
    rng = np.random.default_rng(seed)
    w = World(rng=rng)
    fixed = {
        "bin": SimObject("bin", "bin", 0.10, 0.10,
                         np.array([0.32, -0.25, 0.0, 0.0]), is_region=True),
        "plate": SimObject("plate", "plate", 0.09, 0.02,
                           np.array([0.30, 0.22, 0.0, 0.0]), is_region=True),
    }
    for n, o in fixed.items():
        w.add_object(o)
    placed = [np.array([o.pose[0], o.pose[1]]) for o in fixed.values()]
    names = list(cubes) + (["cyl_1"] if with_cyl else [])
    for name in names:
        if name == "cyl_1":
            obj = SimObject(name, "cylinder", 0.02, 0.08, np.zeros(4))
        else:
            obj = SimObject(name, "cube", CUBE_HALF, CUBE_TOP, np.zeros(4))

        def sample(p):
            return (all(np.linalg.norm(p - q) >= 0.20 for q in placed)
                    and np.linalg.norm(p - HOME[:2]) > 0.15)

        p = None
        for _attempt in range(400):
            cand = rng.uniform([-0.35, -0.30], [0.25, 0.30])
            if sample(cand):
                p = cand
                break
        if p is None:
            # best-effort: maximise the minimum distance to everything
            cands = [rng.uniform([-0.35, -0.30], [0.25, 0.30])
                     for _ in range(800)]
            cands = [c for c in cands if np.linalg.norm(c - HOME[:2]) > 0.15]
            p = max(cands, key=lambda c: min(
                (np.linalg.norm(c - q) for q in placed), default=1.0))
        obj.pose = np.array([p[0], p[1], rest_z(obj), rng.uniform(0, np.pi)])
        placed.append(p)
        w.add_object(obj)
    return w


def trajectory_waypoints(start: np.ndarray, grasp: np.ndarray) -> list:
    """Standard pick/place approach: above-target then insert."""
    above = np.array([grasp[0], grasp[1], grasp[2] + 0.08])
    return [above, grasp[:3] if len(grasp) > 3 else grasp]
