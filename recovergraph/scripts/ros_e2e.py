"""M4: ROS 2 end-to-end bring-up (over the in-process bus shim).

Wires all six packages' real node code over the fixed interfaces and runs
full episodes through them:
  task:      /parse_instruction + /task/goal -> /task/graph
  sim:       /rg_sim/truth|/joint_states|/gripper/state + /rg_sim/execute_skill
  perception /scene/objects -> /belief/state  (frame-driven, noise model)
  detection  /ledger/entry -> /diagnosis  (+ /probe_suggestion)
  recovery   /task/graph (stale/scale/subgoal payload) + /diagnosis ->
             /recovery/decision (+ /recovery/execute action)
  execution  /skill/execute action -> /check_trajectory -> /rg_sim/execute_skill
  safety     /check_trajectory (independent veto) + /safety/verdict
  verify     /verify_effect (spec 6.7 verified state update)

Algorithms stay in rg_core (nodes delegate); the episode orchestration is the
RosEpisodeRunner, a subclass of the in-process EpisodeRunner whose IO seams
(observation, execution, verification, diagnosis, decision) route through
nodes. Acceptance evidence written to experiments/results/ros_e2e/:
  - 100+ episodes through the ROS stack, reconciled against the in-process
    harness (distributional parity; noise realisations differ by design)
  - bag replay determinism (spec Section 13): recorded /ledger/entry fed
    through the diagnoser reproduces identical /diagnosis outputs
  - safety re-proven on the real message flow (unsafe goal -> veto -> abort,
    no execution)
  - decision-path latency (divergence detected -> recovery decision issued)

Run:  python -m scripts.ros_e2e [--episodes 100] [--backend mujoco]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

# make the shim + repo + ROS package sources importable ------------------- #
SHIM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "ros2_ws", "shim")
if SHIM not in sys.path:
    sys.path.insert(0, SHIM)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
ROS_SRC = os.path.join(REPO, "ros2_ws", "src")
for _pkg in ("rg_sim", "rg_task", "rg_perception", "rg_recovery", "rg_exec"):
    _p = os.path.join(ROS_SRC, _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rclpy  # noqa: E402
from rclpy.node import Node  # noqa: E402

from rg_msgs.msg import (BeliefState as BeliefMsg, FaultPosterior,  # noqa: E402
                         LedgerEntry as LedgerMsg, ObjectEstimateArray,
                         RecoveryDecisionMsg, TaskGraphMsg)
from rg_msgs.srv import (ParseInstruction, ProbeSuggestion, Reobserve,  # noqa: E402
                         VerifyEffect)
from rg_msgs.action import SkillExecute  # noqa: E402

from rgsim import pipeline as pl  # noqa: E402
from rgsim.pipeline import EpisodeRunner, cubes_for_task  # noqa: E402
from rg_core.taskgen import make_task  # noqa: E402
from rg_core.types import (BeliefState, Diagnosis, RecoveryDecision,  # noqa: E402
                            SkillResult)
from systems import make_system, RecoverGraph  # noqa: E402

from rg_sim.sim_bridge_node import SimBridgeNode  # noqa: E402
from rg_perception.scene_observer_node import SceneObserverNode  # noqa: E402
from rg_perception.state_estimator_node import StateEstimatorNode  # noqa: E402
from rg_recovery.effect_verifier_node import EffectVerifierNode  # noqa: E402
from rg_recovery.fault_diagnoser_node import FaultDiagnoserNode  # noqa: E402
from rg_recovery.recovery_planner_node import RecoveryPlannerNode  # noqa: E402
from rg_exec.skill_executor_node import SkillExecutorNode  # noqa: E402
from rg_exec.safety_supervisor_node import SafetySupervisorNode  # noqa: E402
from rg_task.task_parser_node import TaskParserNode  # noqa: E402
from rg_task.task_planner_node import TaskPlannerNode  # noqa: E402

ACTIVE = {"bringup": None}          # for the observe() patch
_ORIG_OBSERVE = pl.observe             # in-process observation (restored per pair)


# --------------------------------------------------------------------------- #
class Bringup:
    """All six packages on one bus (fresh per episode)."""

    def __init__(self, seed: int, cubes: list, backend: str = "kinematic"):
        rclpy.init()
        rclpy.reset_bus()
        self.sim = SimBridgeNode(seed=seed, cubes=cubes, backend=backend)
        self.scene = SceneObserverNode(seed=seed * 31 + 7)
        self.estimator = StateEstimatorNode(seed=seed * 17 + 3)
        self.verifier = EffectVerifierNode()
        self.diagnoser = FaultDiagnoserNode()
        self.planner = RecoveryPlannerNode()
        self.executor = SkillExecutorNode()
        self.safety = SafetySupervisorNode()
        self.parser = TaskParserNode()
        self.task_planner = TaskPlannerNode()
        self.driver = _DriverNode()

    def publish_task_goal(self, task) -> None:
        """task flow: /task/goal -> task_planner -> /task/graph."""
        msg = TaskGraphMsg()
        msg.task_id = task.task_id
        msg.goal = task.goal
        graph = json.loads(task.to_json())
        graph.update({"stale": [], "subgoal": "", "scale": 1.0})
        msg.graph_json = json.dumps(graph)
        self.driver.pub_goal.publish(msg)      # /task/goal
        return msg

    def parse_instruction(self, instruction: str) -> str:
        req = ParseInstruction.Request(instruction=instruction)
        resp = self.driver.cli_parse.call_async(req).result()
        return resp.error


class _DriverNode(Node):
    """Episode driver's publishers/subscriptions on the same bus."""

    def __init__(self):
        super().__init__("episode_driver")
        self.pub_goal = self.create_publisher(TaskGraphMsg, "/task/goal", 10)
        self.pub_graph = self.create_publisher(TaskGraphMsg, "/task/graph", 10)
        self.pub_ledger = self.create_publisher(LedgerMsg, "/ledger/entry", 10)
        self.pub_diagnosis = self.create_publisher(FaultPosterior,
                                                   "/diagnosis", 10)
        self.cli_parse = self.create_client(ParseInstruction,
                                            "/parse_instruction")
        self.cli_reobserve = self.create_client(Reobserve, "/reobserve")
        self.cli_probe = self.create_client(ProbeSuggestion,
                                            "/probe_suggestion")
        self.cli_verify = self.create_client(VerifyEffect, "/verify_effect")
        self.pub_scene = self.create_publisher(ObjectEstimateArray,
                                                "/scene/objects", 10)
        self.latest_belief: BeliefMsg | None = None
        self.latest_decision: RecoveryDecisionMsg | None = None
        self.latest_diagnosis: FaultPosterior | None = None
        self.sub_belief = self.create_subscription(
            BeliefMsg, "/belief/state", self.cb_belief, 10)
        self.sub_decision = self.create_subscription(
            RecoveryDecisionMsg, "/recovery/decision", self.cb_decision, 10)
        self.sub_diagnosis = self.create_subscription(
            FaultPosterior, "/diagnosis", self.cb_diagnosis, 10)

    def cb_belief(self, msg):
        self.latest_belief = msg

    def cb_decision(self, msg):
        self.latest_decision = msg

    def cb_diagnosis(self, msg):
        self.latest_diagnosis = msg


# --------------------------------------------------------------------------- #
def ros_observe(world, rng, cfg, occluded=None):
    """Patch target for rgsim.pipeline.observe: the perception chain."""
    b = ACTIVE["bringup"]
    b.sim.publish_world(occluded=occluded or set())
    est = b.driver.latest_belief
    if est is None:
        raise RuntimeError("no belief produced by the perception chain")
    return BeliefState.from_json(est.belief_json)


def ros_reobserve_alt(b) -> BeliefState:
    resp = b.driver.cli_reobserve.call_async(
        Reobserve.Request(viewpoint_hint="alt")).result()
    b.driver.pub_scene.publish(resp.scene)
    est = b.driver.latest_belief
    return BeliefState.from_json(est.belief_json)


class RosSkillRouter:
    """Presents the pipeline's skill-call seam but routes execution through
    the /skill/execute action (safety check + sim bridge delegation)."""

    def __init__(self, runner):
        self.r = runner

    def __getitem__(self, key):
        def call(world, *args):
            return self._route(key, world, *args)
        return call

    def _route(self, skill, world, *args):
        b = self.r.bringup
        if skill == "pick":
            obj, rng, p_grasp, active = args
            args_json = json.dumps({"obj": obj})
            skill_params = {"p_grasp": p_grasp}
            fault = active
        elif skill == "move":
            target, yaw, rng, p_slip, active = args
            args_json = json.dumps(
                {"target": [float(x) for x in np.asarray(target)],
                 "yaw": float(yaw)})
            skill_params = {"p_slip": p_slip}
            fault = active
        else:
            target, yaw, rng, active = args
            args_json = json.dumps(
                {"target": [float(x) for x in np.asarray(target)],
                 "yaw": float(yaw)})
            skill_params = {}
            fault = active
        safety = self.r._last_safety_payload()
        params = {"skill": skill_params, "safety": safety}
        if fault:
            params["skill"]["fault"] = fault["fault"]
            params["skill"]["fault_params"] = dict(fault.get("params", {}))
        goal = SkillExecute.Goal()
        goal.episode_id = self.r.ep_id
        goal.skill = skill
        goal.args_json = args_json
        goal.params_json = json.dumps(params)
        gh, result = b.executor.action_server.send_goal(goal)
        if result.status == "vetoed":
            return SkillResult("abort", telemetry={
                "status": "vetoed", "events": ["safety_veto"],
                "violated": json.loads(result.telemetry_json or "{}").
                get("violated", [])})
        tel = json.loads(result.telemetry_json or "{}")
        tel.setdefault("events", [])
        return SkillResult(result.status or "ok", telemetry=tel)


# --------------------------------------------------------------------------- #
class RosEpisodeRunner(EpisodeRunner):
    """EpisodeRunner whose IO seams route through the ROS message flow."""

    def __init__(self, system, task, seed, condition, bringup,
                 backend="kinematic", faults=None, db=None, ep_id=None):
        self.bringup = bringup
        ACTIVE["bringup"] = bringup
        pl.observe = ros_observe            # perception seam
        super().__init__(system, task, seed=seed, condition=condition,
                         backend=backend, faults=faults, db=db, ep_id=ep_id)
        self.world = bringup.sim.world       # the bridge owns the world
        self._skills = RosSkillRouter(self)
        self._last_wps = []

    # stash the planned waypoints for the executor's safety payload
    def _waypoints_for(self, sub, command, params):
        wps, target = super()._waypoints_for(sub, command, params)
        self._last_wps = wps
        self._last_target = np.asarray(target)[:3]
        return wps, target

    def _last_safety_payload(self) -> dict:
        objs = [[n, [float(x) for x in pos], float(r)]
                for n, pos, r in getattr(self, "_last_safety_objs", [])]
        return {"waypoints": [{"t": round(float(t), 2),
                               "pos": [float(p[0]), float(p[1]), float(p[2])]}
                              for t, p in self._last_wps],
                "dt": 0.5, "objects": objs,
                "held": self.world.held or ""}

    # verified state update through /verify_effect (spec 6.7)
    def _verify_effect(self, sub, command):
        pred = self.fm.predict(sub.skill, sub.args, self.belief, command)
        expected, observed = {}, {}
        for var, (mean, cov) in pred.expected_poses.items():
            name = var[len("pose("):-1]
            expected[var] = {"mean": [float(x) for x in mean],
                             "cov_diag": [float(x) for x in cov]}
            if name == "ee":
                observed[var] = {
                    "mean": [float(x) for x in self.belief.ee_pose],
                    "cov_diag": [float(x) for x in self.belief.ee_cov_diag]}
            else:
                est = self.belief.objects.get(name)
                if est is not None and est.visible:
                    observed[var] = {
                        "mean": [float(x) for x in est.pose],
                        "cov_diag": [float(x) for x in est.cov_diag]}
        exp_disc, obs_disc = {}, {}
        for var, (expected_v, _conf) in pred.expected_discrete.items():
            if var.startswith("holding("):
                obj = var[len("holding("):-1]
                exp_disc[var] = bool(expected_v)
                obs_disc[var] = (self.world.held == obj)
            elif var == "gripper_empty":
                exp_disc[var] = bool(expected_v)
                obs_disc[var] = (self.world.held is None)
        msg = LedgerMsg()
        msg.expected_effects_json = json.dumps(expected)
        msg.observed_state_json = json.dumps(observed)
        msg.entry_json = json.dumps({"expected_discrete": exp_disc,
                                     "observed_discrete": obs_disc,
                                     "skill": sub.skill})
        req = VerifyEffect.Request(entry=msg)
        resp = self.bringup.driver.cli_verify.call_async(req).result()
        return bool(resp.verified), float(resp.confidence)

    # alt-view reobservation through the /reobserve service
    def _reobserve(self, alt_view: bool = False) -> BeliefState:
        self.actions_used += 1
        if alt_view:
            self.occlusion.clear()
            self.belief = ros_reobserve_alt(self.bringup)
        else:
            self.belief = ros_observe(self.world, self.rng, self.noise_cfg,
                                      occluded=set())
        self._log("scene_observer", "reobserve", {"alt_view": alt_view})
        return self.belief


# --------------------------------------------------------------------------- #
class RosRecoverGraph(RecoverGraph):
    """RG whose diagnosis + probe + decision run through the recovery nodes
    (ledger -> diagnoser -> /probe_suggestion -> planner -> /recovery/decision).
    Decision-path latency (divergence detected -> decision issued) is measured
    around the full message round-trip."""

    name = "RG-ROS"

    def __init__(self, bringup):
        self.bringup = bringup
        self.latencies: list = []

    def handle_divergence(self, ctx: dict) -> dict:
        t0 = time.perf_counter()
        b = self.bringup
        driver = b.driver
        # 1) ledger entry -> /diagnosis (the diagnoser node computes it)
        entry = ctx.get("entry")
        payload = {"evidence": ctx["evidence"], "skill": ctx["skill"],
                   "entry": entry.to_dict() if entry is not None else {}}
        msg = LedgerMsg()
        msg.divergence_flagged = True
        msg.entry_json = json.dumps(payload)
        driver.pub_ledger.publish(msg)
        diag_msg = driver.latest_diagnosis
        diag = Diagnosis(
            posterior=dict(zip(diag_msg.hypotheses, diag_msg.posterior)),
            argmax=diag_msg.argmax, entropy=diag_msg.entropy,
            top2_gap=diag_msg.top2_gap,
            evidence=json.loads(diag_msg.evidence_json or "{}"),
            probes=json.loads(diag_msg.probes_json or "[]"))
        # 2) probe suggestion through /probe_suggestion
        probes_used = []
        if any(ctx["evidence"].values()):
            resp = driver.cli_probe.call_async(
                ProbeSuggestion.Request(posterior=diag_msg)).result()
            if resp.probe_needed:
                outcome = ctx["facade"].execute_probe(resp.probe_type)
                from rg_core.diagnosis import PROBES, update_with_probe
                post2 = update_with_probe(diag.posterior,
                                          PROBES[resp.probe_type], outcome)
                diag = Diagnosis.from_posterior(post2,
                                                evidence=ctx["evidence"],
                                                probes=[(resp.probe_type,
                                                         outcome)])
                probes_used = [(resp.probe_type, outcome)]
                diag_msg.posterior_json = json.dumps(post2)
                diag_msg.hypotheses = list(post2.keys())
                diag_msg.posterior = [float(v) for v in post2.values()]
                diag_msg.argmax = diag.argmax
                diag_msg.entropy = diag.entropy
                diag_msg.top2_gap = diag.top2_gap
        # 3) decision through the planner node: graph context + posterior
        graph_msg = TaskGraphMsg()
        graph = {"stale": list(ctx.get("stale") or []),
                 "subgoal": ctx.get("subgoal_id", ""),
                 "scale": float(ctx.get("scale", 1.0))}
        graph_msg.graph_json = json.dumps(graph)
        driver.pub_graph.publish(graph_msg)
        driver.latest_decision = None
        driver.pub_diagnosis.publish(diag_msg)
        dec = driver.latest_decision
        decision = RecoveryDecision(
            chosen=dec.chosen,
            params=json.loads(dec.params_json or "{}"),
            cost_terms=json.loads(dec.cost_terms_json or "{}"),
            feasible=[tuple(x) for x in json.loads(dec.feasible_json or "[]")],
            escalate=bool(dec.escalate),
            report=json.loads(dec.escalation_report_json or "{}"))
        self.latencies.append((time.perf_counter() - t0) * 1000.0)
        if decision.escalate:
            return {"action": "escalate", "decision": decision,
                    "diagnosis": diag}
        resp = {"action": "recovery", "recovery_type": decision.chosen,
                "decision": decision, "diagnosis": diag}
        if ctx.get("kind") == "invariant" and ctx.get("repair_subgoal"):
            resp["repair_subgoal"] = ctx["repair_subgoal"]
        return resp


# --------------------------------------------------------------------------- #
def run_episode_pair(seed: int, horizon: int, condition: str, backend: str,
                     fault: str | None, record: bool):
    """One e2e episode through the ROS stack + the matched in-process episode
    (same seed/task/condition; noise realisations differ by design)."""
    task = make_task(horizon, seed % 20, seed)
    cubes = cubes_for_task(task)
    b = Bringup(seed, cubes, backend=backend)
    err = b.parse_instruction("put red cube in the bin, then put blue cube "
                              "in the bin")
    b.publish_task_goal(task)
    if record:
        rclpy.start_recording()
    rg_ros = RosRecoverGraph(b)
    runner = RosEpisodeRunner(rg_ros, task, seed=seed, condition=condition,
                              bringup=b, backend=backend,
                              faults=[fault] if fault else None)
    res_ros = runner.run()
    if record:
        bag = rclpy.stop_recording()
    else:
        bag = []
    # matched in-process episode (original observation seam restored)
    pl.observe = _ORIG_OBSERVE
    task2 = make_task(horizon, seed % 20, seed)
    res_inproc = EpisodeRunner(make_system("RG"), task2, seed=seed,
                                condition=condition, backend=backend,
                                faults=[fault] if fault else None).run()
    pl.observe = ros_observe
    return {"ros": res_ros, "inproc": res_inproc, "bag": bag,
            "latencies": rg_ros.latencies}


def reconcile(pairs: list) -> dict:
    def rate(rows, key):
        return float(np.mean([bool(r[key]) for r in rows]))
    ros = [p["ros"]["metrics"] for p in pairs]
    inp = [p["inproc"]["metrics"] for p in pairs]
    diag_ros = [m["diag_correct"] for m in ros if m["diag_correct"] is not None]
    diag_inp = [m["diag_correct"] for m in inp if m["diag_correct"] is not None]
    return {
        "n_episodes": len(pairs),
        "success_ros": rate(ros, "success"),
        "success_inproc": rate(inp, "success"),
        "extra_actions_ros": float(np.mean([m["extra_actions"] for m in ros])),
        "extra_actions_inproc": float(np.mean([m["extra_actions"] for m in inp])),
        "diag_accuracy_ros": (float(np.mean(diag_ros))
                              if diag_ros else None),
        "diag_accuracy_inproc": (float(np.mean(diag_inp))
                                 if diag_inp else None),
        "unsafe_ros": int(sum(m["unsafe_executions"] for m in ros)),
        "unsafe_inproc": int(sum(m["unsafe_executions"] for m in inp)),
        "vetoes_ros": int(sum(m["vetoes"] for m in ros)),
        "flags_ros": int(sum(m["flags"] for m in ros)),
        "dispatches_ros": int(sum(m["dispatches"] for m in ros)),
    }


def replay_determinism(bag: list) -> dict:
    """Spec Section 13: recorded ledger entries fed back through the
    diagnoser reproduce identical posteriors (fresh bus, diagnoser only)."""
    ledger_msgs = [rec for rec in bag if rec["channel"] == "/ledger/entry"
                   and rec["kind"] == "topic"]
    diag_msgs = [rec for rec in bag if rec["channel"] == "/diagnosis"
                 and rec["kind"] == "topic"
                 and rec.get("source") == "fault_diagnoser"]
    rclpy.reset_bus()
    node = FaultDiagnoserNode()          # only the diagnoser on the bus
    captured = []
    node.create_subscription(FaultPosterior, "/diagnosis",
                             lambda msg: captured.append(msg.to_dict()), 10)
    for rec in ledger_msgs:
        msg = LedgerMsg.from_dict(rec["payload"])
        node.cb_ledger(msg)
    n = min(len(captured), len(diag_msgs))
    match = sum(1 for a, brec in zip(captured, diag_msgs)
                if a == brec["payload"])
    return {"ledger_entries": len(ledger_msgs), "diagnoses": len(diag_msgs),
            "replayed": n, "identical": match,
            "deterministic": n > 0 and match == n}


def safety_veto_on_message_flow() -> dict:
    """Adversarial: an unsafe goal sent straight to /skill/execute is vetoed
    by the supervisor node and never reaches the simulator."""
    b = Bringup(99, ["red_cube", "blue_cube"], backend="kinematic")
    unsafe_traj = {"waypoints": [{"t": 0.0, "pos": [0.15, 0.10, 0.03]},
                                   {"t": 0.5, "pos": [0.30, 0.10, 0.03]}],
                   "dt": 0.5,
                   "objects": [["red_cube", [0.40, 0.10, 0.03], 0.04],
                               ["bin", [0.35, -0.25, 0.05], 0.10]],
                   "held": ""}
    goal = SkillExecute.Goal()
    goal.episode_id = "safety-probe"
    goal.skill = "move"
    goal.args_json = json.dumps({"target": [0.3, 0.1, 0.1], "yaw": 0.0})
    goal.params_json = json.dumps({"skill": {}, "safety": unsafe_traj})
    ee_before = b.sim.world.ee.copy()
    gh, result = b.executor.action_server.send_goal(goal)
    moved = float(np.linalg.norm(b.sim.world.ee[:3] - ee_before[:3]))
    return {"vetoed": result.status == "vetoed",
            "goal_aborted": not gh.is_succeeded(),
            "world_untouched": moved < 1e-9}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=100)
    ap.add_argument("--backend", default="mujoco",
                    choices=["kinematic", "mujoco"])
    ap.add_argument("--out", default="experiments/results/ros_e2e")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    pairs = []
    bag = None
    t0 = time.time()
    conditions = [("none", None), ("single", None)] + \
        [("single", f) for f in ("f_miss", "f_slip", "f_collision",
                                  "f_pose", "f_controller")]
    for i in range(args.episodes):
        condition, fault = conditions[i % len(conditions)]
        horizon = 1 + (i % 4)
        seed = 9000 + i * 7
        rec = bag is None and i in (2, 4, 5, 6, 7, 8, 9)
        pair = run_episode_pair(seed, horizon, condition, args.backend,
                                fault, record=rec)
        if rec and pair["bag"]:
            n_ledger = sum(1 for r in pair["bag"]
                           if r["channel"] == "/ledger/entry")
            if n_ledger >= 2:
                bag = pair["bag"]
        pairs.append(pair)
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{args.episodes} episodes "
                  f"({time.time() - t0:.1f}s)")

    rec = reconcile(pairs)
    lat = [x for p in pairs for x in p["latencies"]]
    lat_stats = {
        "n": len(lat),
        "p50_ms": float(np.percentile(lat, 50)) if lat else None,
        "p95_ms": float(np.percentile(lat, 95)) if lat else None,
        "max_ms": float(np.max(lat)) if lat else None,
    }
    bus_stats = rclpy.latency_stats()
    rep = replay_determinism(bag or [])
    safety = safety_veto_on_message_flow()

    report = {
        "backend": args.backend,
        "episodes": args.episodes,
        "wall_time_s": round(time.time() - t0, 1),
        "reconciliation": rec,
        "decision_path_latency": lat_stats,
        "bus_latency_ms": {k: {kk: round(vv, 4) for kk, vv in v.items()}
                           for k, v in bus_stats.items()},
        "message_counts": rclpy.message_counts(),
        "bag_replay": rep,
        "safety_veto_on_message_flow": safety,
    }
    with open(os.path.join(args.out, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2, default=float)
    print(json.dumps({k: report[k] for k in
                      ("reconciliation", "decision_path_latency",
                       "bag_replay", "safety_veto_on_message_flow")},
                     indent=2, default=float))
    print(f"report -> {args.out}/report.json")


if __name__ == "__main__":
    main()
