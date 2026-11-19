"""Physics-backend skills (ADR-001): identical signatures and telemetry
vocabulary to rgsim.skills, mechanisms relocated to contact level.

Grasp close = alignment + seeded success (documented simplification, kept
identical to the kinematic backend so fault-manifestation rates match);
everything AFTER the grasp is physics: falls, bounces, tips, slides, stacks
and knock-on contacts all settle through MuJoCo. The held object is pinned to
the hand (scripted attach) and physically contacts free bodies it sweeps.
"""
from __future__ import annotations

import numpy as np

from rg_core.types import GripperState, SkillResult

from .mujoco_world import MuJoCoWorld
from .skills import DT, _force_profile

ALIGN_TOL_XY = 0.03       # grasp alignment tolerance (m, lateral): within
                          # the parallel gripper's finger-recentering span
                          # (open 8 cm vs 5 cm cube -> ~3 cm tolerance)
ALIGN_TOL_Z = 0.045


def _ee_path(wps) -> list:
    return [[round(float(t), 2), [round(float(p[0]), 3), round(float(p[1]), 3),
                                   round(float(p[2]), 3)]]
            for t, p in wps]


def execute_pick(world: MuJoCoWorld, obj: str, rng: np.random.Generator,
                 p_grasp: float, fault: dict | None = None) -> SkillResult:
    o = world.objects[obj]
    events = []
    commanded = o.pose[:3].copy()
    grasp_success_p = p_grasp
    if fault and fault["fault"] == "f_controller":
        # forced abort before the close: approach only, then timeout
        wps = world.move_ee(np.array([commanded[0], commanded[1],
                                      commanded[2] + 0.08, 0.0]))
        return SkillResult("timeout", telemetry={
            "skill": "pick", "obj": obj,
            "commanded_grasp": commanded.tolist(), "grasp_success": False,
            "gripper_width": world.gripper.width,
            "force_profile": _force_profile(rng, 0.0, 0.0, 1),
            "events": ["forced_timeout", "controller_abort"],
            "ee_path": _ee_path(wps)})
    if fault and fault["fault"] == "f_miss":
        ang = rng.uniform(0, 2 * np.pi)
        d = fault["params"]["offset"]
        commanded = commanded + np.array([d * np.cos(ang), d * np.sin(ang), 0.0])
        grasp_success_p = 0.05
        events.append(f"grasp_offset={d * 100:.1f}cm")
    wps = world.move_ee(np.array([commanded[0], commanded[1],
                                  commanded[2] + 0.08, 0.0]))
    wps += world.move_ee(np.array([commanded[0], commanded[1],
                                   commanded[2], 0.0]))
    t0 = world.sim_t
    # ---- contact-level grasp close ----
    aligned = (float(np.linalg.norm(o.pose[:2] - commanded[:2])) <= ALIGN_TOL_XY
               and abs(float(o.pose[2] - commanded[2])) <= ALIGN_TOL_Z)
    eff_p = grasp_success_p if (aligned or fault and
                                 fault["fault"] == "f_miss") else 0.02
    success = bool(rng.random() < eff_p)
    if success:
        world.attach(obj, commanded)          # pin: rides with the hand
        width = 2 * o.half
        hold = float(rng.normal(25.0, 2.0))
        world.gripper = GripperState("holding", width, hold, hold)
        events.append("grasp_closed")
        status = "ok"
        force = _force_profile(rng, hold, t0, 1)
    else:
        world.gripper = GripperState("grasping", 0.004, rng.uniform(2, 6), 25.0)
        events.append("gripper_closed_narrow")
        status = "ok"  # skill ran; the EFFECT diverged (holding false)
        force = _force_profile(rng, 3.0, t0, 1)
    return SkillResult(status=status, telemetry={
        "skill": "pick", "obj": obj,
        "commanded_grasp": commanded.tolist(),
        "grasp_success": success, "gripper_width": world.gripper.width,
        "force_profile": force, "events": events,
        "ee_path": _ee_path(wps),
    })


def execute_move(world: MuJoCoWorld, target3: np.ndarray, yaw: float,
                  rng: np.random.Generator, p_slip: float,
                  fault: dict | None = None) -> SkillResult:
    events = []
    fault = fault or {}
    start = world.ee.copy()
    tgt = np.asarray(target3, float)
    # executed path = the profile the safety supervisor checks: ascend to
    # travel height, move laterally, descend (a straight diagonal from the
    # pick position would cut through the table plane and any object along
    # it - physically executed here)
    travel = 0.22
    pts = [start[:3].copy(),
           np.array([start[0], start[1], travel]),
           np.array([tgt[0], tgt[1], max(travel, tgt[2] + 0.10)]),
           tgt[:3]]
    path = []
    prev = pts[0]
    for q in pts[1:]:
        d = float(np.linalg.norm(q - prev))
        m = max(1, int(np.ceil(d / 0.1)))
        for k in range(1, m + 1):
            path.append(prev + (q - prev) * (k / m))
        prev = q
    n = max(2, len(path))
    if fault.get("fault") == "f_slip":
        events.append(f"friction_mult={fault['params']['friction_mult']}")
    if fault.get("fault") == "f_controller":
        events.append("forced_timeout")
    # identical draw order to the kinematic backend (stream parity)
    drop_at = (int(rng.integers(1, max(2, n - 1)))
               if fault.get("fault") == "f_slip" else None)
    abort_at = (int(rng.integers(1, max(2, n)))
                if fault.get("fault") == "f_controller" else None)
    victim_at = (int(rng.integers(0, n))
                 if fault.get("fault") == "f_collision"
                 and fault["params"].get("victim") else None)
    victim = fault["params"].get("victim") if fault.get("fault") == "f_collision" \
        else None
    wps, dropped, aborted = [], False, False
    for i, p in enumerate(path):
        frac = i / (n - 1) if n > 1 else 1.0
        y = start[3] + (yaw - start[3]) * frac
        if victim_at is not None and i == victim_at:
            shift = world.kick(victim)
            events.append(f"object_displaced:{victim}:"
                          f"{float(np.linalg.norm(shift)) * 100:.1f}cm")
        if drop_at is not None and i == drop_at and world.held is not None:
            world.drop_held()
            events.append("slip_drop")
            dropped = True
        elif world.held is not None and rng.random() < p_slip / max(1, n):
            world.drop_held()
            events.append("slip_drop")
            dropped = True
        seg = world.move_ee(np.array([p[0], p[1], p[2], y]))
        wps.append(seg[-1])
        if abort_at is not None and i == abort_at:
            aborted = True
            events.append("controller_abort")
            break
    if not aborted:
        seg = world.move_ee(np.array([tgt[0], tgt[1], tgt[2], yaw]))
        wps.append(seg[-1])
        status = "ok"
    else:
        status = "timeout"
    prof = _force_profile(rng, world.gripper.force, float(wps[0][0][0]) if
                          isinstance(wps[0][0], list) else float(wps[0][0]),
                          len(wps), drop_at=1 if dropped else None)
    return SkillResult(status, telemetry={
        "skill": "move", "ee_path": _ee_path(wps),
        "force_profile": prof, "events": events,
        "gripper_width": world.gripper.width, "dropped": dropped})


def execute_place(world: MuJoCoWorld, target3: np.ndarray, yaw: float,
                  rng: np.random.Generator,
                  fault: dict | None = None) -> SkillResult:
    events = []
    if world.held is None:
        return SkillResult("abort", telemetry={
            "skill": "place", "events": ["no_object_held"],
            "force_profile": [], "gripper_width": world.gripper.width})
    if fault and fault["fault"] == "f_controller":
        return SkillResult("timeout", telemetry={
            "skill": "place", "obj": world.held,
            "commanded_target": np.asarray(target3).tolist(),
            "events": ["forced_timeout", "controller_abort"],
            "gripper_width": world.gripper.width,
            "force_profile": _force_profile(rng, world.gripper.force, 0.0, 1),
            "ee_path": []})
    release = np.asarray(target3, float).copy()
    if fault and fault["fault"] == "f_pose":
        ang = rng.uniform(0, 2 * np.pi)
        d = fault["params"]["pos"]
        release[:2] += np.array([d * np.cos(ang), d * np.sin(ang)])
        yaw = yaw + np.deg2rad(fault["params"]["yaw_deg"]) * rng.choice([-1, 1])
        events.append(f"release_perturbed={d * 100:.1f}cm/"
                      f"{fault['params']['yaw_deg']:.0f}deg")
    obj = world.held
    wps = world.move_ee(np.array([release[0], release[1], release[2] + 0.05,
                                  yaw]))
    wps += world.move_ee(np.array([release[0], release[1], release[2], yaw]))
    # controlled release: unpin, let physics settle onto the surface
    world.detach_at(release)
    o = world.objects[obj]
    events.append("released")
    return SkillResult("ok", telemetry={
        "skill": "place", "obj": obj,
        "commanded_target": np.asarray(target3).tolist(),
        "final_pose": o.pose.tolist(), "events": events,
        "gripper_width": world.gripper.width,
        "force_profile": _force_profile(rng, 0.0, 0.0, 2),
        "ee_path": _ee_path(wps)})
