"""Episode runner (spec 4 architecture, 6.5-6.8, 10.5 metrics).

Orchestrates one episode: task graph execution with
  - safety-supervised skill execution (independent veto),
  - forward-model prediction + Mahalanobis divergence detection (6.2),
  - verified state update from an independent camera signal (6.7),
  - system-specific divergence handling via SystemAdapter (10.3),
  - dependency-aware invalidation (6.5),
  - recovery macros, loop guard, escalation with oracle-human (6.6, 9),
  - full logging to the episode DB (5.5) and metric collection (10.5).

Controlled variables across systems: same skills, same perception noise,
same planner, same seeds (spec 10.3) - only the recovery logic differs.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

import numpy as np

from rg_core.divergence import check_divergence, chi2_sf, mahalanobis_D
from rg_core.forward_models import ForwardModel, SkillParams
from rg_core.recovery import (PFAIL, RISK, AFFORDANCES, choose_recovery,
                              record_recovery_failure)
from rg_core.safety import SafetySupervisor
from rg_core.task_graph import TaskGraph
from rg_core.types import (FAULTS, BeliefState, Diagnosis, LedgerEntry,
                           RecoveryDecision)
from rg_core.diagnosis import (PROBES, diagnose, suggest_probe,
                               update_with_probe)

from .fault_injector import DIAGNOSABLE, victim_object
from .backends import get_skills, make_world
from .noise import NoiseCfg, observe

MAX_DISPATCHES = 6
VERIFIED_CONF = 0.9          # spec 6.7
ALPHA_STEP = 0.005           # tuned so fault-free FA stays < 2% (spec 6.2)
# scene-level monitoring covariance (spec 6.2/6.3): untouched objects
# are expected at their last verified pose within this tolerance
SCENE_COV = np.array([0.012 ** 2, 0.012 ** 2, 0.012 ** 2, 0.15 ** 2])


def cubes_for_task(task) -> list:
    """Objects the scene must contain for a task (shared by the in-process
    runner, the ROS bring-up and the batch runner so scenes pair exactly)."""
    names = set()
    for s in task.subgoals.values():
        obj = s.args.get("obj")
        if obj:
            names.add(obj)
        target = s.args.get("target", "")
        if target.startswith("on("):
            names.add(target[3:-1])       # stack base cube
        elif target and target not in ("bin", "plate"):
            names.add(target)
    cubes = sorted(n for n in names if n not in ("bin", "plate"))
    return cubes or ["red_cube", "blue_cube"]


class SimFacade:
    """What system adapters may do to the world: re-observe or run a
    disambiguating probe. Every call counts as an action."""

    def __init__(self, runner: "EpisodeRunner"):
        self.r = runner

    def reobserve(self, alt_view: bool = False) -> BeliefState:
        return self.r._reobserve(alt_view=alt_view)

    def execute_probe(self, name: str) -> str:
        return self.r._execute_probe(name)


class SubgoalOutcome:
    def __init__(self, status, entry=None, div=None, result=None,
                 command=None, verified_conf=0.0, sub_id=None):
        self.status = status            # verified | diverged | precondition_fail | vetoed
        self.entry = entry
        self.div = div
        self.result = result
        self.command = command or {}
        self.verified_conf = verified_conf
        self.sub_id = sub_id


class EpisodeRunner:
    def __init__(self, system, task: TaskGraph, seed: int, condition: str = "none",
                 stratify: str = "random", noise_cfg: NoiseCfg | None = None,
                 db=None, ep_id: str | None = None,
                 skill_params: SkillParams | None = None,
                 backend: str = "kinematic",
                 faults: list | None = None):
        self.system = system
        self.task = task
        self.seed = seed
        self.condition = condition
        self.backend = backend
        self._skills = get_skills(backend)
        self.schedule = {}
        from .fault_injector import episode_schedule
        skills = []
        for sid in task.topo_order():
            sub = task.subgoal(sid)
            if sub.skill == "move":
                tag = "move_transport" if sub.args.get("obj") else "move_free"
            else:
                tag = sub.skill
            skills.append(tag)
        for ev in episode_schedule(seed, task.horizon(), condition, stratify,
                                   skills=skills, faults=faults):
            self.schedule[ev.step] = ev
        self.noise_cfg = noise_cfg or NoiseCfg()
        self.db = db
        self.ep_id = ep_id or f"ep_{seed:06d}"
        self.fm = ForwardModel(skill_params or SkillParams())
        self.supervisor = SafetySupervisor()
        self.world = make_world(seed, self._cubes_for_task(), backend=backend)
        self.rng = np.random.default_rng(seed + 1)
        self.belief = observe(self.world, self.rng, self.noise_cfg)
        self.last_verified = {n: o.pose.copy() for n, o in self.world.objects.items()}
        self.plan = task.topo_order()
        self.nominal = len(self.plan)
        self.budget = self.nominal + max(12, 2 * self.nominal + 4)
        self.actions_used = 0
        self.stale: set = set()
        self.verified_effects: dict = {}
        self.loop_guard: dict = {}
        self.attempts: dict = defaultdict(int)
        self.occlusion: dict = {}   # obj name -> sim_t until which masked
        self.metrics = dict(success=False, steps=0, extra_actions=0,
                            recovery_time_s=0.0, vetoes=0, unsafe_executions=0,
                            interventions=0, flags=0, flags_fault_free=0,
                            dispatches=0,
                            diag_correct=None, regret=0.0, false_alarm_steps=0,
                            escalations=0)
        self._diverge_t = None
        self._first_diag_scored = False
        self.events: list = []
        self.facade = SimFacade(self)
        self.initial_poses = {n: o.pose.copy() for n, o in self.world.objects.items()}
        if db:
            db.new_episode(self.ep_id, seed, task.task_id, task.horizon(),
                           system.name,
                           [e.to_dict() for e in self.schedule.values()],
                           json.dumps({n: o.pose.tolist()
                                       for n, o in self.world.objects.items()}))

    # ------------------------------------------------------------------ #
    def _cubes_for_task(self):
        return cubes_for_task(self.task)

    def _log(self, node: str, etype: str, payload: dict):
        self.events.append((node, etype, payload))
        if self.db:
            self.db.log_event(self.ep_id, self.world.sim_t, node, etype, payload)

    # ------------------------------------------------------------------ #
    # Belief / facts
    # ------------------------------------------------------------------ #
    def _reobserve(self, alt_view: bool = False) -> BeliefState:
        self.actions_used += 1
        if alt_view:
            self.occlusion.clear()  # new viewpoint resolves masked regions
        occ = {n for n, until in self.occlusion.items()
               if self.world.sim_t < until}
        self.belief = observe(self.world, self.rng, self.noise_cfg,
                              occluded=occ)
        self._log("scene_observer", "reobserve", {"alt_view": alt_view})
        return self.belief

    def _execute_probe(self, name: str) -> str:
        self.actions_used += 1
        if name == "gripper_squeeze":
            outcome = "grip_stable" if (self.world.held is not None
                                        and self.world.gripper.force > 10.0) \
                else "empty_gripper"
        else:  # reobserve_alt_view: answer from ground truth
            outcome = self._reobserve_truth_outcome()
        self._log("fault_diagnoser", "probe", {"probe": name, "outcome": outcome})
        return outcome

    def _reobserve_truth_outcome(self) -> str:
        # which object are we worried about? the most recent divergence target
        name = self._probe_target
        if name is None or name not in self.world.objects:
            return "obj_as_expected"
        o = self.world.objects[name]
        init = self.initial_poses[name]
        verified = self.last_verified.get(name, init)
        at_source = np.linalg.norm(o.pose[:3] - init[:3]) < 0.02
        as_expected = np.linalg.norm(o.pose[:3] - verified[:3]) < 0.03
        if self.occlusion.get(name, 0.0) > self.world.sim_t:
            return "still_occluded"
        if at_source and not as_expected:
            return "obj_at_source"
        if as_expected:
            return "obj_as_expected"
        return "obj_displaced"

    _probe_target = None

    def _facts(self, belief: BeliefState | None = None) -> set:
        belief = belief or self.belief
        facts = set()
        if self.world.held is not None:
            facts.add(f"holding({self.world.held})")
        else:
            facts.add("gripper_empty")
        for name, est in belief.objects.items():
            if name in ("bin", "plate"):
                continue
            for r in ("bin", "plate"):
                if np.hypot(est.pose[0] - belief.objects[r].pose[0],
                            est.pose[1] - belief.objects[r].pose[1]) <= \
                        self.world.objects[r].half:
                    facts.add(f"in({name},{r})")
        names = [n for n in belief.objects if n not in ("bin", "plate")]
        for a in names:
            for b in names:
                if a != b:
                    pa, pb = belief.objects[a].pose, belief.objects[b].pose
                    if np.hypot(pa[0] - pb[0], pa[1] - pb[1]) < 0.03 and \
                            0.02 < pa[2] - pb[2] < 0.075:
                        facts.add(f"on({a},{b})")
        return facts

    def _pre_ok(self, sub, belief: BeliefState | None = None) -> bool:
        belief = belief or self.belief
        facts = self._facts(belief)
        for p in sub.pre:
            if p == "gripper_empty":
                if self.world.held is not None:
                    return False
            elif p == "reachable(obj)" or p.startswith("reachable("):
                continue  # always reachable within our workspace
            elif p.startswith("on(") and p.endswith("(table)"):
                continue
            elif p not in facts:
                return False
        return True

    # ------------------------------------------------------------------ #
    # Commands, execution, detection
    # ------------------------------------------------------------------ #
    def _command_for(self, sub, belief: BeliefState | None = None,
                     clearance: float = 0.0) -> dict:
        belief = belief or self.belief
        if sub.skill == "pick":
            obj = sub.args["obj"]
            est = belief.objects.get(obj)
            pos = est.pose[:3].copy() if est is not None else \
                self.world.objects[obj].pose[:3]
            return {"grasp_pos": pos, "yaw": 0.0}
        if sub.skill == "move":
            tgt = self._region_pose(sub.args["target"], belief)
            # transport moves hand off above the region; free moves hover
            z = 0.12 if sub.args.get("obj") else 0.15
            return {"target": tgt + np.array([0, 0, z + clearance]),
                    "yaw": 0.0,
                    "dist": float(np.linalg.norm(tgt - self.world.ee[:3]))}
        if sub.skill == "place":
            t = sub.args["target"]
            if t.startswith("on("):
                base = t[3:-1]
                tgt = self.world.objects[base].pose[:3] + \
                    np.array([0, 0, self.world.objects[base].height])
            else:
                tgt = self._region_pose(t, belief)
                tgt[2] = 0.05 if t == "bin" else 0.02
            return {"target": tgt + np.array([0, 0, clearance]), "yaw": 0.0}
        raise ValueError(sub.skill)

    def _region_pose(self, name: str, belief) -> np.ndarray:
        est = belief.objects.get(name)
        src = est if est is not None else self.world.objects[name]
        return np.array([src.pose[0], src.pose[1], src.pose[2]])

    def _waypoints_for(self, sub, command, params) -> list:
        """Execution path: ascend to travel height, move laterally, descend.
        Densified at 0.1 m / 0.5 s (0.2 m/s) so the speed check reflects the
        real kinematic trajectory."""
        if sub.skill == "pick":
            g = np.asarray(command["grasp_pos"], float)
            s = self.world.ee[:3]
            pts = [s.copy(),
                   np.array([s[0], s[1], 0.22]),
                   np.array([g[0], g[1], 0.22]),
                   np.array([g[0], g[1], g[2] + 0.08]),
                   np.array([g[0], g[1], g[2]])]
            target_point = g
        elif sub.skill == "move":
            t = np.asarray(command["target"], float)
            pts = [self.world.ee[:3].copy(),
                   np.array([self.world.ee[0], self.world.ee[1], 0.22]),
                   np.array([t[0], t[1], max(0.22, t[2] + 0.10)]),
                   t]
            target_point = t
        else:
            t = np.asarray(command["target"], float)
            pts = [self.world.ee[:3].copy(),
                   np.array([self.world.ee[0], self.world.ee[1], 0.22]),
                   np.array([t[0], t[1], 0.22]),
                   t]
            target_point = t
        # objects the manipulation must legitimately approach (the target
        # object / stack base) are excluded from collision checking, mirroring
        # MoveIt's allowed-touch-object planning; others need 16cm clearance.
        wps = [(0.0, pts[0].copy())]
        t_acc = 0.0
        prev = pts[0]
        for p in pts[1:]:
            d = float(np.linalg.norm(p - prev))
            n = max(1, int(np.ceil(d / 0.1)))
            for i in range(1, n + 1):
                t_acc += 0.5
                wps.append((t_acc, prev + (p - prev) * (i / n)))
            prev = p
        return wps, target_point

    def _safety_object_list(self, sub, target_point) -> list:
        """Objects the supervisor checks collisions against (spec 6.8):
        everything except the held object and objects legitimately
        approachable at the current EE position or the skill's target
        (MoveIt-style allowed-touch). Robust scene spacing (>= 0.20 m
        pairwise, best-effort fallback) guarantees checked bystanders are
        outside physical contact range of the target approach, so this
        exclusion cannot hide a knock-over (M5 redesign note)."""
        excluded = set()
        if self.world.held is not None:
            excluded.add(self.world.held)
        tgt = np.asarray(target_point)[:2]
        for n, o in self.world.objects.items():
            if o.is_region:
                continue
            if min(float(np.linalg.norm(o.pose[:2] - self.world.ee[:2])),
                    float(np.linalg.norm(o.pose[:2] - tgt))) <= 0.16:
                excluded.add(n)
        return [(n, o.pose[:3], o.radius)
                for n, o in self.world.objects.items()
                if not o.is_region and n not in excluded]

    def _execute_subgoal(self, sub_id: str, fault_event=None,
                         params: dict | None = None) -> SubgoalOutcome:
        sub = self.task.subgoal(sub_id)
        params = params or {}
        self._probe_target = sub.args.get("obj")
        if not self._pre_ok(sub):
            return SubgoalOutcome("precondition_fail", sub_id=sub_id)
        command = self._command_for(sub, clearance=float(params.get("clearance", 0.0)))
        # ---- independent safety check (spec 6.8) ----
        wps, target_point = self._waypoints_for(sub, command, params)
        objs = self._safety_object_list(sub, target_point)
        self._last_safety_objs = objs
        verdict = self.supervisor.check_trajectory(wps, dt=0.5, objects=objs,
                                                   held=self.world.held)
        if not verdict.accepted:
            self.metrics["vetoes"] += 1
            self._log("safety_supervisor", "veto", verdict.to_dict())
            # one re-route attempt with extra clearance; still vetoed -> vetoed
            command2 = self._command_for(sub, clearance=0.06)
            wps2, tp2 = self._waypoints_for(sub, command2, params)
            v2 = self.supervisor.check_trajectory(wps2, dt=0.5, objects=objs,
                                                  held=self.world.held)
            if not v2.accepted:
                self.metrics["vetoes"] += 1
                return SubgoalOutcome("vetoed", sub_id=sub_id)
            command = command2
        self.actions_used += 1
        self.metrics["steps"] += 1
        active = None
        if fault_event is not None:
            active = {"fault": fault_event.fault,
                      "params": dict(fault_event.params)}
            if fault_event.fault == "f_collision":
                # resolve the displacement victim at execution time: the held
                # object half the time, else a scene object (spec 10.1)
                victim = self.world.held if (
                    self.world.held is not None and self.rng.random() < 0.5) \
                    else victim_object(self.task, self.world,
                                       self.plan.index(sub_id))
                active["params"]["victim"] = victim
            if fault_event.fault == "f_perception":
                obj = sub.args.get("obj") or self._probe_target
                dur = float(fault_event.params.get("dur_s", 2.0))
                # mask stays active through the skill span plus its duration
                # (spec 10.1: 30-80% of visible area for 1-3 s)
                self.occlusion[obj] = self.world.sim_t + dur + 3.0
        # ---- execute skill by mechanism ----
        p = self.fm.p
        if sub.skill == "pick":
            result = self._skills["pick"](self.world, sub.args["obj"], self.rng,
                                         params.get("p_grasp", p.p_grasp),
                                         active)
        elif sub.skill == "move":
            result = self._skills["move"](self.world, command["target"], 0.0,
                                         self.rng,
                                         params.get("p_slip", p.p_slip),
                                         active)
        else:
            result = self._skills["place"](self.world, command["target"], 0.0,
                                          self.rng, active)
        self._log("skill_executor", "exec", {"subgoal": sub_id,
                                             "status": result.status,
                                             "events": result.telemetry.get("events", [])})
        # ---- observe, predict, divergence (spec 6.2) ----
        belief_after = observe(self.world, self.rng, self.noise_cfg,
                               occluded={n for n, until in self.occlusion.items()
                                         if self.world.sim_t < until})
        pred = self.fm.predict(sub.skill, sub.args, self.belief, command)
        exp_poses, obs_poses, exp_disc, obs_disc = self._detection_inputs(
            sub, pred, belief_after, result,
            point_estimates=bool(getattr(self.system, "point_estimates", False)))
        div = check_divergence(exp_poses, obs_poses, exp_disc, obs_disc,
                               alpha=getattr(self, "alpha_step_override", None)
                               or ALPHA_STEP)
        entry = self._make_entry(sub_id, sub, pred, belief_after, div, result,
                                 fault_event is None,
                                 evidence=self._evidence_features(
                                     sub, result, belief_after)[0])
        self.metrics["flags"] += int(div.flagged)
        if fault_event is None:
            self.metrics["flags_fault_free"] += int(div.flagged)
        if self.db:
            self.db.log_ledger(entry)
        self.belief = belief_after
        return SubgoalOutcome("diverged" if div.flagged else "executed",
                              entry=entry, div=div, result=result,
                              command=command, sub_id=sub_id)

    def _detection_inputs(self, sub, pred, belief_after, result,
                          point_estimates: bool):
        exp_poses, obs_poses = {}, {}
        for var, (mean, cov) in pred.expected_poses.items():
            name = var[len("pose("):-1]
            if name == "ee":
                est_mean = belief_after.ee_pose
                est_cov = belief_after.ee_cov_diag
            else:
                est = belief_after.objects.get(name)
                if est is None or not est.visible:
                    continue
                est_mean, est_cov = est.pose, est.cov_diag
            if point_estimates:
                cov = np.full(len(mean), 1e-4)
                est_cov = np.full(len(mean), 1e-4)
            exp_poses[var] = (mean, cov)
            obs_poses[var] = (est_mean, est_cov)
        # ---- scene-level monitoring (spec 6.2 divergence, 6.3 'scene diff') --
        # every object the skill is NOT supposed to touch is expected to stay
        # at its last verified pose; a displacement (bystander knock, external
        # disturbance) is itself a divergence. This is what makes
        # f_collision separable from f_slip (the displaced-object mechanism,
        # spec 10.1) and what the non_target_object_moved feature presumes.
        touched = {sub.args.get("obj")}
        t = sub.args.get("target", "")
        if t.startswith("on("):
            touched.add(t[3:-1])
        for name, o in self.world.objects.items():
            if o.is_region or name in touched or name == self.world.held:
                continue
            est = belief_after.objects.get(name)
            if est is None or not est.visible:
                continue
            if point_estimates:
                continue          # ablation A3: uncertainty removed entirely
            exp_poses[f"pose({name})"] = (self.last_verified[name].copy(),
                                          SCENE_COV.copy())
            obs_poses[f"pose({name})"] = (est.pose, est.cov_diag)
        exp_disc, obs_disc = {}, {}
        for var, (expected, _conf) in pred.expected_discrete.items():
            if var.startswith("holding("):
                obj = var[len("holding("):-1]
                observed = (self.world.held == obj)
                exp_disc[var] = bool(expected)
                obs_disc[var] = bool(observed)
            elif var == "gripper_empty":
                exp_disc[var] = bool(expected)
                obs_disc[var] = (self.world.held is None)
        return exp_poses, obs_poses, exp_disc, obs_disc

    def _make_entry(self, sub_id, sub, pred, belief_after, div, result,
                    fault_free: bool, evidence: dict | None = None) -> LedgerEntry:
        exp = {v: {"mean": list(map(float, m)), "cov_diag": list(map(float, c))}
               for v, (m, c) in pred.expected_poses.items()}
        obs = {}
        for v in pred.expected_poses:
            name = v[len("pose("):-1]
            if name == "ee":
                obs[v] = {"mean": list(map(float, belief_after.ee_pose)),
                          "cov_diag": list(map(float, belief_after.ee_cov_diag))}
            else:
                est = belief_after.objects.get(name)
                if est is not None and est.visible:
                    obs[v] = {"mean": list(map(float, est.pose)),
                              "cov_diag": list(map(float, est.cov_diag))}
        return LedgerEntry(
            episode_id=self.ep_id, step=self.metrics["steps"], subgoal=sub_id,
            skill=sub.skill,
            preconditions={p: {"predicted": True, "conf": 0.9} for p in sub.pre},
            expected_effects=exp,
            predicted_state_conf=0.9,
            observed_state=obs,
            actual_effects=self._evidence_features(sub, result, belief_after)[1],
            divergence_D=div.max_D, divergence_flagged=div.flagged,
            downstream=self.task.downstream_of(sub_id),
            timestamp=float(self.world.sim_t), fault_free=fault_free,
            evidence=dict(evidence or {}),
            plan_index=(self.plan.index(sub_id)
                        if sub_id in self.plan else -1))

    # ------------------------------------------------------------------ #
    # Evidence extraction (spec 6.3 feature sources)
    # ------------------------------------------------------------------ #
    def _evidence_features(self, sub, result: SkillResult, belief_after):
        tel = result.telemetry
        events = tel.get("events", [])
        target_obj = sub.args.get("obj")
        feats = {
            "gripper_closed_narrow": ("gripper_closed_narrow" in events) or
                (sub.skill == "pick" and tel.get("gripper_width", 0.08) < 0.015
                 and not tel.get("grasp_success", True)),
            "grip_force_drop": ("slip_drop" in events) or (
                sub.skill == "move" and tel.get("force_profile")
                and min(f for _t, f in tel["force_profile"]) <
                0.4 * max(1.0, self.world.gripper.commanded_force)),
            "tracked_obj_final_pose_off":
                any(e.startswith("release_perturbed") for e in events) or (
                    sub.skill == "place" and tel.get("final_pose") is not None and
                    tel.get("commanded_target") is not None and
                    float(np.linalg.norm(np.array(tel["final_pose"][:3]) -
                                         np.array(tel["commanded_target"][:3]))) > 0.04),
            "low_visibility_ee_nominal":
                (target_obj is not None and
                 (self.occlusion.get(target_obj, 0.0) > self.world.sim_t or
                  (target_obj in belief_after.objects and
                   not belief_after.objects[target_obj].visible))),
            "controller_timeout_abort": result.status in ("timeout", "abort") or
                ("controller_abort" in events) or ("forced_timeout" in events),
            "non_target_object_moved": any(
                np.linalg.norm(self.world.objects[n].pose[:3] -
                               self.last_verified[n][:3]) > 0.05
                for n in self.world.objects
                if n not in ("bin", "plate") and n != target_obj),
        }
        notes = []
        if feats["grip_force_drop"]:
            notes.append("grip force drop during transport")
        if feats["gripper_closed_narrow"]:
            notes.append("gripper closed to near-zero width")
        if feats["controller_timeout_abort"]:
            notes.append("controller timeout/abort flag")
        if feats["non_target_object_moved"]:
            notes.append("non-target object moved in scene diff")
        if feats["low_visibility_ee_nominal"]:
            notes.append("object weakly visible while EE pose nominal")
        if feats["tracked_obj_final_pose_off"]:
            notes.append("tracked object final pose off target")
        return feats, notes

    # ------------------------------------------------------------------ #
    # Verified state update (spec 6.7)
    # ------------------------------------------------------------------ #
    def _verify_effect(self, sub, command) -> tuple:
        """Independent camera-only check (spec 6.7): agreement of the observed
        pose with the EE-based prediction inside a verification tolerance.
        confidence = chi2_sf(D_tol, k) >= 0.9 with D_tol computed against the
        task tolerance (2 cm positional, 0.15 rad yaw), not the sensor noise."""
        pred = self.fm.predict(sub.skill, sub.args, self.belief, command)
        tol_cov = np.array([0.03**2, 0.03**2, 0.03**2, 0.2**2])
        worst = 0.0
        k = 4
        for var, (mean, _cov) in pred.expected_poses.items():
            name = var[len("pose("):-1]
            if name == "ee":
                est_mean, est_cov = self.belief.ee_pose, self.belief.ee_cov_diag
            else:
                est = self.belief.objects.get(name)
                if est is None or not est.visible:
                    return False, 0.0
                est_mean, est_cov = est.pose, est.cov_diag
            d = mahalanobis_D(est_mean - mean, tol_cov)
            worst = max(worst, d)
        for var, (expected, _c) in pred.expected_discrete.items():
            if var.startswith("holding("):
                if (self.world.held == var[len("holding("):-1]) != bool(expected):
                    return False, 0.0
            elif var == "gripper_empty":
                if (self.world.held is None) != bool(expected):
                    return False, 0.0
        conf = float(chi2_sf(worst, k))
        return conf >= VERIFIED_CONF, conf

    # ------------------------------------------------------------------ #
    # Recovery macros (spec 6.4 affordances executed by the executor)
    # ------------------------------------------------------------------ #
    def _modified_params(self, rtype: str) -> dict:
        p = {}
        if rtype in ("retry_modified", "reobserve_retry_modified"):
            p["p_grasp"] = min(1.0, self.fm.p.p_grasp + 0.005)
            p["p_slip"] = self.fm.p.p_slip * 0.5
        if rtype == "replan_motion":
            p["clearance"] = 0.06
        return p

    def _regrasp(self, obj: str) -> bool:
        """Re-observe then pick the object at its observed pose. A physical
        gripper holds one object: regrasping while another is held is a
        precondition failure (the recovery layer must not silently swap
        payloads - the scene monitor would flag the dragged object)."""
        if self.world.held is not None and self.world.held != obj:
            return False
        self._reobserve(alt_view=True)
        est = self.belief.objects.get(obj)
        if est is None or not est.visible:
            return False
        out = self._execute_pick_at(obj, est.pose[:3])
        return out

    def _execute_pick_at(self, obj: str, pos) -> bool:
        self.actions_used += 1
        result = self._skills["pick"](self.world, obj, self.rng,
                                      self.fm.p.p_grasp, None)
        self._log("skill_executor", "recovery_pick", {"obj": obj,
                                                      "success": result.telemetry.get("grasp_success")})
        return bool(result.telemetry.get("grasp_success"))

    def _verify_only(self, sub_id: str) -> SubgoalOutcome:
        """Re-verify an already-satisfied effect without physical action
        (e.g. a pick whose holding effect is true but whose pose check was
        blocked by occlusion)."""
        sub = self.task.subgoal(sub_id)
        command = self._command_for(sub)
        ok, conf = self._verify_effect(sub, command)
        return SubgoalOutcome("executed" if ok else "diverged",
                              command=command, sub_id=sub_id,
                              verified_conf=conf)

    def _repair(self, sub_id: str, params: dict | None = None) -> SubgoalOutcome:
        """Generic repair: ensure the subgoal's precondition state, then
        re-execute the subgoal (used by all recovery macros)."""
        sub = self.task.subgoal(sub_id)
        params = params or {}
        if sub.skill == "pick":
            obj = sub.args["obj"]
            if self.world.held == obj:
                # holding already true: re-observe (done by the recovery macro)
                # and re-verify without touching the object
                return self._verify_only(sub_id)
        if sub.skill == "move":
            obj = self.world.held
            if obj is None:
                # object was dropped: regrasp it wherever it is now
                target_obj = self._probe_target or self._find_dropped(sub)
                if target_obj is None or not self._regrasp(target_obj):
                    return SubgoalOutcome("precondition_fail", sub_id=sub_id)
        elif sub.skill == "place":
            obj = sub.args["obj"]
            if self.world.held != obj:
                if not self._regrasp(obj):
                    return SubgoalOutcome("precondition_fail", sub_id=sub_id)
        return self._execute_subgoal(sub_id, None, params)

    def _find_dropped(self, sub) -> str | None:
        # the object this subgoal was supposed to transport
        prev = [s for s in self.task.topo_order()
                if self.task.subgoal(s).skill == "pick"]
        for s in reversed(prev):
            if s in self.verified_effects:
                return self.task.subgoal(s).args["obj"]
        return sub.args.get("obj")

    def _run_recovery(self, rtype: str, sub_id: str, diag: Diagnosis | None,
                      decision: RecoveryDecision | None = None) -> SubgoalOutcome:
        self._log("recovery_planner", "recovery_start",
                  {"type": rtype, "subgoal": sub_id})
        params = self._modified_params(rtype)
        if rtype in ("reobserve_redetect", "reobserve_retry_modified",
                     "reobserve_replan", "regrasp", "rollback"):
            self._reobserve(alt_view=True)
        if rtype == "rollback":
            self.world.move_ee(np.array([0.0, -0.35, 0.12, 0.0]))
            self.actions_used += 1
        if rtype == "reobserve_replan":
            remaining = set(self.plan[self.plan.index(sub_id) + 1:])
            self.stale |= remaining
            return self._repair(sub_id, params)
        out = self._repair(sub_id, params)
        return out

    # ------------------------------------------------------------------ #
    # Invalidation (spec 6.5)
    # ------------------------------------------------------------------ #
    def _invalidate(self, sub_id: str, argmax_fault: str, mode: str) -> None:
        sub = self.task.subgoal(sub_id)
        target_obj = sub.args.get("obj")
        corrupted = []
        if sub.skill == "pick":
            corrupted.append(f"holding({target_obj})")
        elif sub.skill == "move":
            corrupted.append(f"holding({target_obj})" if target_obj else
                             "gripper_empty")
            if target_obj:
                corrupted.append(f"pose({target_obj})")
        elif sub.skill == "place":
            for eff in sub.eff:
                if eff.startswith(("in(", "on(")):
                    corrupted.append(eff)
        if argmax_fault == "f_collision":
            for n, o in self.world.objects.items():
                if not o.is_region:
                    corrupted.append(f"pose({n})")
        if mode == "all":  # ablation A2 / B3 semantics: invalidate everything
            self.stale |= set(self.plan)
            return
        stale, _corrupted = self.task.mark_stale(corrupted)
        self.stale |= stale

    def _producing_subgoal(self, pred: str) -> str | None:
        for sid in self.task.topo_order():
            if pred in self.task.subgoal(sid).eff:
                return sid
        return None

    # ------------------------------------------------------------------ #
    # Dispatch loop for a diverged / failed subgoal
    # ------------------------------------------------------------------ #
    def _handle_failure(self, sub_id: str, outcome: SubgoalOutcome,
                        kind: str) -> str:
        """Run the system's divergence-handling loop. Returns
        'verified' | 'unfixed' | 'abort'."""
        self.metrics["dispatches"] += 1
        sub = self.task.subgoal(sub_id)
        feats, notes = (self._evidence_features(sub, outcome.result,
                                                self.belief)
                        if outcome.result else ({}, ["precondition violated"]))
        dispatches = 0
        repair_sub = kind == "invariant" and outcome.get("repair_sub")
        while dispatches < MAX_DISPATCHES and self.actions_used <= self.budget:
            dispatches += 1
            active_fault = self.schedule.get(self.plan.index(sub_id))
            scale = (self.task.horizon() - self.plan.index(sub_id)) / \
                max(1, self.task.horizon())
            truth = active_fault.label if active_fault else None
            ctx = {
                "entry": outcome.entry, "evidence": feats, "notes": notes,
                "belief": self.belief, "task": self.task,
                "subgoal_id": sub_id, "skill": sub.skill,
                "attempts": self.attempts[sub_id],
                "loop_guard": self.loop_guard, "facade": self.facade,
                "stale": sorted(self.stale), "scale": scale,
                "kind": kind, "truth": truth,
                "repair_subgoal": outcome.get("repair_sub") if kind == "invariant" else None,
            }
            self._probe_target = sub.args.get("obj")
            response = self.system.handle_divergence(ctx)
            action = response.get("action")
            diag = response.get("diagnosis")

            if diag is not None and self.db:
                self.db.log_diagnosis(self.ep_id, self.metrics["steps"], diag,
                                      truth or "none")
            if diag is not None and not self._first_diag_scored and \
                    truth in DIAGNOSABLE:
                self._first_diag_scored = True
                self.metrics["diag_correct"] = (diag.argmax == truth)
            if diag is not None and truth in DIAGNOSABLE and \
                    response.get("decision") is not None:
                oracle = choose_recovery(
                    Diagnosis.from_posterior({f: (1.0 if f == truth else 0.0)
                                              for f in FAULTS}),
                    sub_id, loop_guard={}, scale=scale)
                self.metrics["regret"] += \
                    response["decision"].cost_terms.get("cost", 0.0) - \
                    oracle.cost_terms.get("cost", 0.0)

            if action == "none":
                self._invalidate(sub_id, diag.argmax if diag else "f_none",
                                 "targeted")
                return "unfixed"
            if action == "abort":
                return "abort"
            if action == "retry":
                out2 = self._execute_subgoal(sub_id, None, response.get("params"))
                if out2.status == "executed" and self._finalize(sub_id, out2):
                    return "verified"
                outcome = out2
                feats, notes = self._evidence_features(sub, out2.result,
                                                       self.belief) \
                    if out2.result else (feats, notes)
                continue
            if action == "replanned":
                self._reobserve()
                self._invalidate(sub_id, diag.argmax if diag else "f_none", "all")
                if kind == "invariant" and outcome.get("repair_sub"):
                    rsub = outcome.get("repair_sub")
                    rsub_out = self._repair(rsub)
                    if rsub_out.status == "executed" and \
                            self._finalize(rsub, rsub_out):
                        self.verified_effects[rsub] = True
                out2 = self._execute_subgoal(sub_id, None)
                if out2.status == "executed" and self._finalize(sub_id, out2):
                    return "verified"
                outcome = out2
                continue
            if action == "recovery":
                rtype = response["recovery_type"]
                target = response.get("repair_subgoal") or sub_id
                if response.get("invalidate") == "all":
                    # ablation A2: dependency invalidation removed -
                    # everything downstream is treated as stale
                    self._invalidate(sub_id,
                                     diag.argmax if diag else "f_none", "all")
                out2 = self._run_recovery(rtype, target, diag,
                                          response.get("decision"))
                if out2.status == "executed" and self._finalize(target, out2):
                    if target != sub_id:
                        self.verified_effects[target] = True
                    return "verified"
                argmax = diag.argmax if diag else "f_none"
                record_recovery_failure(self.loop_guard, sub_id, argmax, rtype)
                if self.db and response.get("decision") is not None:
                    self.db.log_recovery(
                        self.ep_id, self.metrics["steps"],
                        response["decision"], result="failed")
                outcome = out2 if out2.entry is not None else outcome
                continue
            if action == "escalate":
                decision = response["decision"]
                self.metrics["interventions"] += 1
                self.metrics["escalations"] += 1
                self._log("human", "escalation", decision.report or {})
                if self.db and decision.report:
                    self.db.log_recovery(self.ep_id, self.metrics["steps"],
                                         decision, result="escalated")
                alts = (decision.report or {}).get("alternatives") or []
                if alts:
                    alt = min(alts, key=lambda a: a.get("risk", 1.0))
                    out2 = self._run_recovery(alt["action"], sub_id, diag)
                    if out2.status == "executed" and self._finalize(sub_id, out2):
                        return "verified"
                    return "abort"   # human attempt failed too
                # no autonomous options: human manual repair (p=0.95)
                if self.rng.random() < 0.95:
                    out2 = self._repair(sub_id)
                    if out2.status == "executed" and self._finalize(sub_id, out2):
                        return "verified"
                return "abort"
        return "abort" if self.actions_used > self.budget else "unfixed"

    def _finalize(self, sub_id: str, outcome: SubgoalOutcome) -> bool:
        """Verified state update after an execution/recovery (spec 6.7)."""
        sub = self.task.subgoal(sub_id)
        if outcome.status != "executed":
            return False
        # camera-based verification with one re-observe retry
        ok, conf = self._verify_effect(sub, outcome.command)
        if not ok:
            self._reobserve()
            ok, conf = self._verify_effect(sub, outcome.command)
        if ok:
            self.verified_effects[sub_id] = True
            if self._diverge_t is not None:
                self.metrics["recovery_time_s"] += \
                    float(self.world.sim_t - self._diverge_t)
                self._diverge_t = None
            for n, o in self.world.objects.items():
                self.last_verified[n] = o.pose.copy()
            self._log("effect_verifier", "verified",
                      {"subgoal": sub_id, "conf": round(conf, 3)})
            if self.db:
                self.db.log_recovery(self.ep_id, self.metrics["steps"],
                                     RecoveryDecision(chosen="verify"),
                                     result="verified")
            return True
        self._log("effect_verifier", "unverified", {"subgoal": sub_id})
        return False

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    def run(self) -> dict:
        t0 = time.time()
        stale_dependency_done = set()
        for i, sub_id in enumerate(self.plan):
            if self.actions_used > self.budget:
                break
            # stale subgoals must re-verify preconditions (spec 6.5 step 3)
            if sub_id in self.stale:
                self._reobserve()
                self.stale.discard(sub_id)
            fault_event = self.schedule.get(i)
            # stale_dependency: displace a previously placed object up front
            if fault_event is not None and \
                    fault_event.fault == "stale_dependency" and \
                    i not in stale_dependency_done:
                stale_dependency_done.add(i)
                victim = self._placed_object() or victim_object(
                    self.task, self.world, i)
                if victim:
                    self.world.displace(victim,
                                        fault_event.params.get("shift", (0.03, 0.15))
                                        if isinstance(fault_event.params.get("shift"), tuple)
                                        else (0.03, 0.15))
                    self._log("fault_injector", "stale_dependency",
                              {"victim": victim})
            # invariant check from observed facts (spec 6.5 step 4)
            self._invariant_sweep(sub_id)
            outcome = self._execute_subgoal(sub_id, fault_event)
            if fault_event is not None and fault_event.fault == "f_collision":
                pass
            if outcome.status == "vetoed":
                res = self._handle_failure(sub_id, outcome, "veto")
                if res != "verified":
                    break
                continue
            if outcome.status == "precondition_fail":
                res = self._handle_failure(sub_id, outcome, "precondition")
                if res == "abort":
                    break
                continue
            if outcome.status == "diverged":
                self._diverge_t = float(self.world.sim_t)
                res = self._handle_failure(sub_id, outcome, "divergence")
                if res == "verified":
                    continue
                if res == "unfixed":
                    continue  # e.g. B1: keep going without recovery
                break  # abort
            # executed cleanly -> verified state update (6.7)
            if not self._finalize(sub_id, outcome):
                self._diverge_t = float(self.world.sim_t)
                res = self._handle_failure(sub_id, outcome, "verification")
                if res != "verified":
                    if res == "unfixed":
                        continue
                    break
        # final invariant sweeps: invariants must hold until END (spec 6.5);
        # the last execution's observation can surface a violation that the
        # per-subgoal check (which runs on the pre-step belief) missed
        for _sweep in range(2):
            if self.actions_used > self.budget:
                break
            if not self.task.check_invariants(self._facts()):
                break
            if not self._invariant_sweep(self.plan[-1] if self.plan else None):
                break
        # episode outcome
        facts = self._facts()
        inv_ok = not self.task.check_invariants(facts)
        success = all(self.verified_effects.get(s, False)
                      for s in self.task.subgoals) and inv_ok \
            and self.actions_used <= self.budget
        self.metrics["success"] = bool(success)
        self.metrics["extra_actions"] = max(0, self.actions_used - self.nominal)
        outcome = "success" if success else \
            ("budget_exceeded" if self.actions_used > self.budget else "failure")
        self.metrics["wall_time"] = time.time() - t0
        self._log("episode_recorder", "outcome",
                  {"outcome": outcome, "metrics": self.metrics})
        if self.db:
            self.db.finish_episode(self.ep_id, outcome, success,
                                   self.metrics["wall_time"], self.metrics)
        return {"outcome": outcome, "metrics": self.metrics,
                "system": self.system.name, "seed": self.seed,
                "task_id": self.task.task_id,
                "horizon": self.task.horizon(),
                "faults": [e.to_dict() for e in self.schedule.values()]}

    def _invariant_sweep(self, sub_id) -> bool:
        """Invariant check + repair dispatch (spec 6.5 step 4). Returns True
        when a violation was handled (the caller should re-check)."""
        violated = self.task.check_invariants(self._facts())
        if not violated:
            return False
        pred = violated[0].split(" must")[0].strip()
        producing = self._producing_subgoal(pred)
        if producing and self.verified_effects.get(producing):
            self.verified_effects[producing] = False
            outcome = SubgoalOutcome("precondition_fail", sub_id=sub_id)
            outcome.set_repair_sub(producing)
            res = self._handle_failure(sub_id, outcome, "invariant")
            self._invalidate(producing, "f_none", "targeted")
            return res != "abort"
        return False

    def _placed_object(self) -> str | None:
        """Most recently placed object (for stale_dependency displacement)."""
        for sid in reversed(self.plan[:max(1, len(self.plan))]):
            sub = self.task.subgoal(sid)
            if sid in self.verified_effects and sub.skill == "place":
                return sub.args["obj"]
        return None


def _patch_subgoal_outcome():
    SubgoalOutcome.set_repair_sub = \
        lambda self, sid: setattr(self, "repair_sub", sid)
    SubgoalOutcome.get = lambda self, k: getattr(self, k, None)


_patch_subgoal_outcome()
