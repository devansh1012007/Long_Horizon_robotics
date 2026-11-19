"""Physics backend world (ADR-001 accepted): MuJoCo tabletop behind the same
surface as the kinematic `rgsim.world.World`.

Contract (verified by tests/test_mujoco_bridge.py):
  objects: dict name -> SimObject (pose mirrors physics; (x, y, z, yaw))
  ee, gripper, held, grasp_offset, sim_t: same semantics as the kinematic world
  move_ee(target, speed): servo the mocap hand to the target through physics
    steps; a held object is pinned to the hand (scripted attach); returns
    waypoints [(t, xyz)] every ~0.1 m of travel.
  displace(name, dist_range): impulse-level bump (arm-sweep abstraction),
    physical slide + settle; returns the measured shift.

Scene geometry is built from the SAME `generate_scene` sampling as the
kinematic world (paired scenes across backends). Rest heights are matched to
the kinematic conventions: bin interior top at 0.025 (object centre rests at
the commanded 0.05), plate is a flush marker (object rests at floor level,
matching the kinematic 0.025), stacking rests at base top + half exactly.

Fault relocation (spec 10.1 mechanisms, contact level where physical):
  f_miss         -> offset commanded grasp -> misaligned close (no attach)
  f_slip         -> mid-transport grasp release with hand velocity + jitter,
                    physical fall, bounce/tip and settle
  f_collision    -> impulse bump of the victim, physical slide + settle
  f_pose         -> perturbed release pose with physical settling
  f_controller   -> servo abort at a random waypoint (hand holds position)
  f_perception   -> observation-level occlusion (not a physics event)
  stale_dep      -> task-graph event, impulse displacement of a placed object
"""
from __future__ import annotations

import numpy as np

from rg_core.types import GripperState

from .world import CUBE_HALF, HOME, SimObject, generate_scene, rest_z

DT = 0.01                 # physics timestep (s)
SERVO_SPEED = 0.2         # m/s nominal hand speed (safety limit is 0.35)
FLOOR_MU = 1.2            # floor-object friction (velocity calibration)
GRAV = 9.81
KICK_VEL = lambda d: float(np.sqrt(2.0 * FLOOR_MU * GRAV * d))
_SETTLE_V_TOL = 0.004     # m/s linear velocity for "at rest"
_SETTLE_W_TOL = 0.05      # rad/s angular velocity
_SETTLE_MAX_STEPS = 400


def _yaw_quat(yaw: float) -> np.ndarray:
    return np.array([np.cos(yaw / 2.0), 0.0, 0.0, np.sin(yaw / 2.0)])


def _quat_yaw(q: np.ndarray) -> float:
    w, x, y, z = float(q[0]), float(q[1]), float(q[2]), float(q[3])
    return float(np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))


class MuJoCoWorld:
    """Same attribute surface as rgsim.world.World, physics under the hood."""

    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.objects: dict = {}
        self.ee: np.ndarray = HOME.copy()
        self.gripper = GripperState()
        self.held: str | None = None
        self.grasp_offset = np.zeros(3)
        self.sim_t = 0.0
        self._model = None
        self._data = None
        self._body_of: dict = {}          # object name -> body id
        self._hand_mid = 0                # mocap id of the hand
        self._qposadr: dict = {}          # object name -> qpos address (7)
        self._qveladr: dict = {}          # object name -> dof address (6)
        self._pin_pending = False

    # ------------------------------------------------------------------ #
    # Scene construction (same sampling as the kinematic world)
    # ------------------------------------------------------------------ #
    @classmethod
    def from_scene(cls, seed: int, cubes: list, with_cyl: bool = True) -> "MuJoCoWorld":
        kin = generate_scene(seed, cubes, with_cyl)
        w = cls(np.random.default_rng(seed))
        xml = _scene_xml(kin)
        import mujoco
        w._model = mujoco.MjModel.from_xml_string(xml)
        w._data = mujoco.MjData(w._model)
        mujoco.mj_forward(w._model, w._data)
        w._hand_mid = int(w._model.body_mocapid[
            mujoco.mj_name2id(w._model, mujoco.mjtObj.mjOBJ_BODY, "hand")])
        for name, o in kin.objects.items():
            w.objects[name] = SimObject(
                name, o.kind, o.half, o.height, o.pose.copy(),
                is_region=o.is_region)
            if not o.is_region:
                bid = mujoco.mj_name2id(w._model, mujoco.mjtObj.mjOBJ_BODY, name)
                w._body_of[name] = bid
                jid = mujoco.mj_name2id(w._model, mujoco.mjtObj.mjOBJ_JOINT,
                                        f"{name}_j")
                w._qposadr[name] = w._model.jnt_qposadr[jid]
                w._qveladr[name] = w._model.jnt_dofadr[jid]
        w._sync_objects()
        return w

    # ------------------------------------------------------------------ #
    # Physics plumbing
    # ------------------------------------------------------------------ #
    def _step(self, n: int = 1) -> None:
        import mujoco
        for _ in range(n):
            if self.held is not None:
                self._pin_held()
            mujoco.mj_step(self._model, self._data)
            self.sim_t += DT
        self._sync_objects()

    def _pin_held(self) -> None:
        """Scripted attach: the held object rides rigidly with the hand."""
        name = self.held
        q = _yaw_quat(float(self.ee[3]))
        pos = self._data.mocap_pos[self._hand_mid] + self.grasp_offset
        qa = self._qposadr[name]
        self._data.qpos[qa:qa + 3] = pos
        self._data.qpos[qa + 3:qa + 7] = q
        va = self._qveladr[name]
        self._data.qvel[va:va + 6] = 0.0

    def _sync_objects(self) -> None:
        for name, bid in self._body_of.items():
            p = self._data.xpos[bid]
            yaw = _quat_yaw(self._data.xquat[bid])
            o = self.objects[name]
            o.pose[0], o.pose[1], o.pose[2], o.pose[3] = p[0], p[1], p[2], yaw
        self.ee = np.array([*self._data.mocap_pos[self._hand_mid],
                            _quat_yaw(self._data.mocap_quat[self._hand_mid])])

    def settle(self, max_steps: int = _SETTLE_MAX_STEPS) -> None:
        """Step physics until all free bodies are at rest (or cap reached)."""
        import mujoco
        for _ in range(max_steps):
            self._step()
            qv = self._data.qvel
            lin = float(np.max(np.abs(qv[:3]))) if len(qv) else 0.0
            ok = all(
                np.max(np.abs(self._data.qvel[self._qveladr[n]:
                                               self._qveladr[n] + 3]))
                <= _SETTLE_V_TOL and
                np.max(np.abs(self._data.qvel[self._qveladr[n] + 3:
                                               self._qveladr[n] + 6]))
                <= _SETTLE_W_TOL
                for n in self._qveladr)
            _ = lin
            if ok:
                break

    # ------------------------------------------------------------------ #
    # Public surface (parity with rgsim.world.World)
    # ------------------------------------------------------------------ #
    def add_object(self, obj: SimObject) -> None:
        raise NotImplementedError("physics scenes are built in from_scene")

    def region(self, name: str) -> SimObject:
        return self.objects[name]

    def object_top(self, name: str) -> np.ndarray:
        o = self.objects[name]
        return np.array([o.pose[0], o.pose[1], o.pose[2] + o.height, 0.0])

    def move_ee(self, target: np.ndarray, speed: float = SERVO_SPEED) -> list:
        """Servo the hand to the target at `speed` m/s through physics steps.
        Target: 3-vector (keep yaw) or 4-vector [x, y, z, yaw]. Returns
        waypoints [(t, xyz)] spaced ~0.1 m along the path."""
        tgt = np.asarray(target, float)
        yaw = float(tgt[3]) if tgt.size > 3 else float(self.ee[3])
        tgt3 = tgt[:3]
        start = self._data.mocap_pos[self._hand_mid].copy()
        yaw0 = _quat_yaw(self._data.mocap_quat[self._hand_mid])
        dist = float(np.linalg.norm(tgt3 - start))
        if dist < 1e-9:
            self._data.mocap_quat[self._hand_mid] = _yaw_quat(yaw)
            self._step(1)
            return [(self.sim_t, start.copy())]
        travel = 0.0
        next_wp = 0.0
        wps: list = [(self.sim_t, start.copy())]
        per_step = speed * DT
        while True:
            cur = self._data.mocap_pos[self._hand_mid]
            d = tgt3 - cur
            s = float(np.linalg.norm(d))
            if s <= per_step:
                self._data.mocap_pos[self._hand_mid] = tgt3
                self._data.mocap_quat[self._hand_mid] = _yaw_quat(yaw)
                self._step(1)
                wps.append((self.sim_t, tgt3.copy()))
                break
            adv = min(per_step, s)
            self._data.mocap_pos[self._hand_mid] = cur + d / s * adv
            travel += adv
            frac = min(1.0, travel / dist)
            self._data.mocap_quat[self._hand_mid] = _yaw_quat(
                yaw0 + (yaw - yaw0) * frac)
            self._step(1)
            if travel >= next_wp + 0.1:
                next_wp += 0.1
                wps.append((self.sim_t,
                            self._data.mocap_pos[self._hand_mid].copy()))
        self._sync_objects()
        return wps

    def in_region(self, obj_name: str, region: str) -> bool:
        o, r = self.objects[obj_name], self.objects[region]
        return float(np.hypot(o.pose[0] - r.pose[0], o.pose[1] - r.pose[1])) <= r.half

    def on_top(self, a: str, b: str) -> bool:
        pa, pb = self.objects[a].pose, self.objects[b].pose
        return (float(np.hypot(pa[0] - pb[0], pa[1] - pb[1])) < 0.03
                and abs(pa[2] - (pb[2] + self.objects[b].height)) < 0.02)

    def kick(self, name: str, dist_range=(0.03, 0.15)) -> np.ndarray:
        """Impulse bump (arm-sweep abstraction for f_collision): velocity
        calibrated to slide `d` metres under Coulomb friction, then settle.
        A held object is knocked out of the gripper first (kinematic
        `displace` semantics). Returns the measured displacement."""
        if self.held == name:
            self.held = None
            self.gripper = GripperState("lost", 0.08, 0.5,
                                        self.gripper.commanded_force)
        before = self.objects[name].pose[:2].copy()
        ang = float(self.rng.uniform(0, 2 * np.pi))
        d = float(self.rng.uniform(*dist_range))
        v = KICK_VEL(d)
        va = self._qveladr[name]
        self._data.qvel[va:va + 3] = 0.0
        self._data.qvel[va] = v * np.cos(ang)
        self._data.qvel[va + 1] = v * np.sin(ang)
        self._data.qvel[va + 2] = 0.0
        self.settle()
        after = self.objects[name].pose[:2]
        return np.array([after[0] - before[0], after[1] - before[1], 0.0])

    def displace(self, name: str, dist_range=(0.03, 0.15)) -> np.ndarray:
        """External disturbance (stale_dependency / displaced-object fault):
        physical impulse + settle. If the object is held, it is lost."""
        if self.held == name:
            self.held = None
            self.gripper = GripperState("lost", 0.08, 0.5,
                                        self.gripper.commanded_force)
        return self.kick(name, dist_range)

    def drop_held(self, jitter: float = 0.05) -> str | None:
        """Release the grasp mid-transport (slip mechanism): the object keeps
        the hand's velocity plus jitter, then falls and settles physically."""
        name = self.held
        if name is None:
            return None
        va = self._qveladr[name]
        jit = self.rng.normal(0, jitter, 3) * np.array([1, 1, 0])
        # hand moves at `speed`; direction unknown post-hoc -> small kick
        self._data.qvel[va:va + 3] = jit
        self.held = None
        self.gripper = GripperState("lost", 0.08, 0.5,
                                    self.gripper.commanded_force)
        self.settle()
        return name

    def attach(self, name: str, commanded: np.ndarray) -> None:
        """Successful grasp close: pin the object at the hand."""
        o = self.objects[name]
        off = o.pose[:3] - np.asarray(commanded, float)
        off[2] = 0.0
        self.grasp_offset = off
        self.held = name

    def detach_at(self, target: np.ndarray) -> None:
        """Controlled release at a place target: unpin + settle."""
        name = self.held
        if name is None:
            return
        va = self._qveladr[name]
        self._data.qvel[va:va + 6] = 0.0
        self.held = None
        self.gripper = GripperState("empty", 0.08, 0.0, 0.0)
        self.settle()


def _scene_xml(kin_world) -> str:
    """Build the MJCF from a sampled kinematic scene (identical layout)."""
    parts = [
        '<mujoco model="recovergraph">',
        '<option timestep="0.01" integrator="implicitfast" '
        'gravity="0 0 -9.81"/>',
        '<worldbody>',
        '<geom name="floor" type="plane" size="0.8 0.8 0.05" '
        'friction="1.2 0.6 0.002" rgba="0.9 0.9 0.9 1"/>',
    ]
    # invisible containment walls
    for name, pos, size in (
        ("wall_xp", (0.55, 0, 0.2), (0.02, 0.62, 0.2)),
        ("wall_xn", (-0.55, 0, 0.2), (0.02, 0.62, 0.2)),
        ("wall_yp", (0, 0.55, 0.2), (0.62, 0.02, 0.2)),
        ("wall_yn", (0, -0.55, 0.2), (0.62, 0.02, 0.2)),
    ):
        parts.append(f'<geom name="{name}" type="box" pos="{pos[0]} {pos[1]} '
                      f'{pos[2]}" size="{size[0]} {size[1]} {size[2]}" '
                      f'rgba="0 0 0 0" contype="0" conaffinity="0"/>')
    # hand: non-colliding mocap marker (grasp = scripted attach; bumps are
    # impulse-level; held objects still contact free bodies physically)
    parts.append('<body name="hand" mocap="true" '
                 f'pos="{HOME[0]} {HOME[1]} {HOME[2]}">'
                 '<geom name="hand_g" type="box" size="0.02 0.02 0.02" '
                 'rgba="0.3 0.5 0.9 0.4" contype="0" conaffinity="0"/>'
                 '</body>')
    for name, o in kin_world.objects.items():
        if o.is_region:
            continue
        x, y, z, yaw = (float(v) for v in o.pose)
        q = _yaw_quat(yaw)
        if o.kind == "cylinder":
            geom = (f'<geom name="{name}_g" type="cylinder" '
                    f'size="{o.half} {o.height / 2:.4f}" mass="0.08" '
                    f'friction="1.0 0.5 0.002" rgba="0.95 0.75 0.2 1"/>')
        else:
            geom = (f'<geom name="{name}_g" type="box" '
                    f'size="{o.half:.4f} {o.half:.4f} {o.half:.4f}" '
                    f'mass="0.1" friction="1.0 0.5 0.002" '
                    f'rgba="0.9 0.2 0.2 1"/>')
        parts.append(f'<body name="{name}" pos="{x:.6f} {y:.6f} {z:.6f}" '
                      f'quat="{q[0]:.6f} {q[1]:.6f} {q[2]:.6f} {q[3]:.6f}">'
                      f'<joint name="{name}_j" type="free" '
                      f'damping="0.02"/>{geom}</body>')
    # bin: raised tray interior so a released object rests with its centre at
    # the commanded 0.05 (kinematic parity); walls keep it inside the region
    bin_o = kin_world.objects["bin"]
    bx, by = float(bin_o.pose[0]), float(bin_o.pose[1])
    parts.append(f'<geom name="bin_floor" type="box" pos="{bx:.4f} {by:.4f} '
                  f'0.0125" size="{bin_o.half:.3f} {bin_o.half:.3f} 0.0125" '
                  f'friction="1.0 0.5 0.002" rgba="0.4 0.4 0.45 1"/>')
    for wn, dx, dy in (("bin_wx+", 1, 0), ("bin_wx-", -1, 0),
                       ("bin_wy+", 0, 1), ("bin_wy-", 0, -1)):
        parts.append(
            f'<geom name="{wn}" type="box" pos="{bx + dx * bin_o.half:.4f} '
            f'{by + dy * bin_o.half:.4f} 0.045" size="0.004 0.004 0.04" '
            f'friction="1.0 0.5 0.002" rgba="0.4 0.4 0.45 1"/>')
    # plate: flush non-colliding marker (object rests on the floor plane,
    # matching the kinematic rest z)
    pl = kin_world.objects["plate"]
    parts.append(f'<geom name="plate_g" type="cylinder" '
                 f'pos="{float(pl.pose[0]):.4f} {float(pl.pose[1]):.4f} 0.001" '
                 f'size="{pl.half:.3f} 0.001" contype="0" conaffinity="0" '
                 f'rgba="0.85 0.85 0.9 1"/>')
    parts.append('</worldbody></mujoco>')
    return "".join(parts)
