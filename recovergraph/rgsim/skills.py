"""Scripted manipulation skills with probabilistic outcomes and fault hooks
(spec 6.1 forward models, spec 10.1 injection mechanisms).

Each skill returns a SkillResult whose telemetry feeds evidence extraction
(spec 6.3) and the B5 fault-pipeline baseline. Fault injection happens by
mechanism (offset grasp target, reduced friction, disturbed release pose,
occlusion window, displaced object, forced timeout), so ground-truth labels
follow the injected mechanism (spec 10.1 honesty rule).
"""
from __future__ import annotations

import numpy as np

from rg_core.types import GripperState, SkillResult

from .world import World, rest_z

DT = 0.5  # seconds per waypoint


def _force_profile(rng, hold_force: float, t0: float, n: int,
                   drop_at: int | None = None) -> list:
    prof = []
    for i in range(n):
        f = max(0.0, rng.normal(hold_force, 1.5))
        if drop_at is not None and i >= drop_at:
            f = rng.uniform(0.0, 2.0)  # force collapse after a slip
        prof.append((round(t0 + i * DT, 2), round(float(f), 2)))
    return prof


def execute_pick(world: World, obj: str, rng: np.random.Generator,
                 p_grasp: float, fault: dict | None = None) -> SkillResult:
    o = world.objects[obj]
    events = []
    commanded = o.pose[:3].copy()
    grasp_success_p = p_grasp
    if fault and fault["fault"] == "f_controller":
        # forced abort before the close (spec 10.1: controller failure at a
        # random point in the trajectory)
        wps = world.move_ee(np.array([commanded[0], commanded[1],
                                      commanded[2] + 0.08, 0.0]))
        world.sim_t += DT
        return SkillResult("timeout", telemetry={
            "skill": "pick", "obj": obj,
            "commanded_grasp": commanded.tolist(), "grasp_success": False,
            "gripper_width": world.gripper.width,
            "force_profile": _force_profile(rng, 0.0, 0.0, 1),
            "events": ["forced_timeout", "controller_abort"],
            "ee_path": [[round(float(t), 2), p.tolist()] for t, p in wps]})
    if fault and fault["fault"] == "f_miss":
        # offset grasp target 2-6 cm lateral (spec 10.1) -> grasp misses
        ang = rng.uniform(0, 2 * np.pi)
        d = fault["params"]["offset"]
        commanded = commanded + np.array([d * np.cos(ang), d * np.sin(ang), 0.0])
        grasp_success_p = 0.05
        events.append(f"grasp_offset={d * 100:.1f}cm")
    approach = np.array([commanded[0], commanded[1], commanded[2] + 0.08, 0.0])
    wps = world.move_ee(approach)
    world.sim_t += DT
    wps += world.move_ee(np.array([commanded[0], commanded[1], commanded[2], 0.0]))
    t0 = world.sim_t
    world.sim_t += DT
    success = bool(rng.random() < grasp_success_p)
    if success:
        # object stays physically where it was; it hangs offset from the EE
        world.grasp_offset = o.pose[:3] - commanded
        world.grasp_offset[2] = 0.0
        world.held = obj
        width = 2 * o.half
        hold = float(rng.normal(25.0, 2.0))
        world.gripper = GripperState("holding", width, hold, hold)
        o.pose[:3] = world.ee[:3] + world.grasp_offset
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
        "ee_path": [[round(float(t), 2), p.tolist()] for t, p in wps],
    })


def _drop(world: World, rng, events: list) -> bool:
    o = world.objects[world.held]
    jitter = rng.normal(0, 0.05, 3) * np.array([1, 1, 0])
    o.pose[:3] = np.clip(world.ee[:3] + world.grasp_offset + jitter, -0.42, 0.42)
    from .world import rest_z
    o.pose[2] = rest_z(o)
    world.held = None
    world.gripper = GripperState("lost", 0.08, 0.5,
                                 world.gripper.commanded_force)
    events.append("slip_drop")
    return True


def execute_move(world: World, target3: np.ndarray, yaw: float,
                 rng: np.random.Generator, p_slip: float,
                 fault: dict | None = None) -> SkillResult:
    events = []
    fault = fault or {}
    start = world.ee.copy()
    tgt = np.asarray(target3, float)
    # executed path = the profile the safety supervisor checks: ascend to
    # travel height, move laterally, descend (a straight diagonal from the
    # pick position would cut through the table plane and any object along
    # it - physically executed by the MuJoCo backend)
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
        t = world.sim_t + i * DT
        frac = i / (n - 1) if n > 1 else 1.0
        wps.append((t, p.copy()))
        if world.held is not None:
            o = world.objects[world.held]
            o.pose[:3] = p + world.grasp_offset
            o.pose[3] = start[3] + (yaw - start[3]) * frac
        if victim_at is not None and i == victim_at:
            shift = world.displace(victim)
            events.append(f"object_displaced:{victim}:"
                          f"{float(np.linalg.norm(shift)) * 100:.1f}cm")
        if drop_at is not None and i == drop_at and world.held is not None:
            dropped = _drop(world, rng, events)
        elif world.held is not None and rng.random() < p_slip / max(1, n):
            dropped = _drop(world, rng, events)
        if abort_at is not None and i == abort_at:
            aborted = True
            events.append("controller_abort")
            break
    world.sim_t += len(wps) * DT
    if not aborted:
        world.ee = np.array([tgt[0], tgt[1], tgt[2], yaw])
        if world.held is not None:
            o = world.objects[world.held]
            o.pose[:3] = world.ee[:3] + world.grasp_offset
            o.pose[3] = yaw
        status = "ok"
    else:
        status = "timeout"
    prof = _force_profile(rng, world.gripper.force, float(wps[0][0]), len(wps),
                          drop_at=1 if dropped else None)
    return SkillResult(status, telemetry={
        "skill": "move", "ee_path": [[round(float(t), 2), p.tolist()]
                                     for t, p in wps],
        "force_profile": prof, "events": events,
        "gripper_width": world.gripper.width, "dropped": dropped})


def execute_place(world: World, target3: np.ndarray, yaw: float,
                  rng: np.random.Generator,
                  fault: dict | None = None) -> SkillResult:
    events = []
    if world.held is None:
        return SkillResult("abort", telemetry={
            "skill": "place", "events": ["no_object_held"],
            "force_profile": [], "gripper_width": world.gripper.width})
    if fault and fault["fault"] == "f_controller":
        # abort just before the release: object stays held, effect missing
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
    wps = world.move_ee(np.array([release[0], release[1], release[2] + 0.05, yaw]))
    world.sim_t += DT
    wps += world.move_ee(np.array([release[0], release[1], release[2], yaw]))
    obj = world.held
    noise = rng.normal(0, 0.004, 4)
    o = world.objects[obj]
    # the object rests at the release height (bin mouth / stack top); the
    # kinematic sim does not model free fall to a lower rest surface
    o.pose = np.array([release[0] + noise[0], release[1] + noise[1],
                       max(rest_z(o), release[2]), yaw + noise[3]])
    world.held = None
    world.gripper = GripperState("empty", 0.08, 0.0, 0.0)
    world.sim_t += DT
    events.append("released")
    return SkillResult("ok", telemetry={
        "skill": "place", "obj": obj,
        "commanded_target": np.asarray(target3).tolist(),
        "final_pose": o.pose.tolist(), "events": events,
        "gripper_width": world.gripper.width,
        "force_profile": _force_profile(rng, 0.0, 0.0, 2),
        "ee_path": [[round(float(t), 2), p.tolist()] for t, p in wps]})
