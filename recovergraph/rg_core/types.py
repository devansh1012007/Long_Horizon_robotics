"""Core data types for RecoverGraph (spec Sections 4-6).

Pure stdlib + numpy; no ROS dependencies so rg_core can be unit-tested and
reused inside ROS 2 nodes unchanged.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np

# Fault hypotheses (spec 5.4). f_none means "no fault"; the diagnoser only
# runs on flagged steps, so its hypothesis space is these seven labels.
FAULTS = [
    "f_none",
    "f_miss",
    "f_slip",
    "f_pose",
    "f_collision",
    "f_perception",
    "f_controller",
]
# Labels injected by the fault injector (spec 10.1). "stale_dependency" is a
# dependency-level fault that is handled by invalidation, not by the Bayes
# diagnoser, hence excluded from diagnosis-accuracy scoring.
INJECTED_LABELS = FAULTS + ["stale_dependency"]

GRIPPER_STATES = ("empty", "grasping", "holding", "lost")


# --------------------------------------------------------------------------- #
# Belief state (spec 5.3)
# --------------------------------------------------------------------------- #
@dataclass
class ObjectEst:
    """Per-object pose estimate: SE(3)-lite mean [x, y, z, yaw] + diag covariance."""

    name: str
    pose: np.ndarray          # (4,) metres / radians
    cov_diag: np.ndarray      # (4,)
    identity_conf: float = 1.0
    visible: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "pose": [float(x) for x in self.pose],
            "cov_diag": [float(x) for x in self.cov_diag],
            "identity_conf": self.identity_conf,
            "visible": self.visible,
        }

    @staticmethod
    def from_dict(d: dict) -> "ObjectEst":
        return ObjectEst(
            name=d["name"],
            pose=np.array(d["pose"], float),
            cov_diag=np.array(d["cov_diag"], float),
            identity_conf=d.get("identity_conf", 1.0),
            visible=d.get("visible", True),
        )


@dataclass
class GripperState:
    state: str = "empty"          # empty | grasping | holding | lost
    width: float = 0.08           # metres between fingertips
    force: float = 0.0            # measured grip force (N)
    commanded_force: float = 0.0  # commanded grip force (N)

    def to_dict(self) -> dict:
        return {
            "state": self.state, "width": self.width,
            "force": self.force, "commanded_force": self.commanded_force,
        }

    @staticmethod
    def from_dict(d: dict) -> "GripperState":
        return GripperState(**d)


@dataclass
class BeliefState:
    """Belief s_t = (q_t, o_t, g_t, h_t, c_t); h_t (ledger history) is held by
    the caller/DB, not duplicated here."""

    t: float
    ee_pose: np.ndarray          # (4,) end-effector pose estimate
    ee_cov_diag: np.ndarray      # (4,)
    gripper: GripperState
    objects: dict = field(default_factory=dict)   # name -> ObjectEst
    confidence: dict = field(default_factory=dict)  # var -> conf in [0, 1]

    def copy(self) -> "BeliefState":
        return BeliefState(
            t=self.t,
            ee_pose=self.ee_pose.copy(),
            ee_cov_diag=self.ee_cov_diag.copy(),
            gripper=GripperState(**self.gripper.to_dict()),
            objects={k: ObjectEst.from_dict(v.to_dict()) for k, v in self.objects.items()},
            confidence=dict(self.confidence),
        )

    def to_dict(self) -> dict:
        return {
            "t": self.t,
            "ee_pose": [float(x) for x in self.ee_pose],
            "ee_cov_diag": [float(x) for x in self.ee_cov_diag],
            "gripper": self.gripper.to_dict(),
            "objects": {k: v.to_dict() for k, v in self.objects.items()},
            "confidence": dict(self.confidence),
        }

    @staticmethod
    def from_dict(d: dict) -> "BeliefState":
        return BeliefState(
            t=d["t"],
            ee_pose=np.array(d["ee_pose"], float),
            ee_cov_diag=np.array(d["ee_cov_diag"], float),
            gripper=GripperState.from_dict(d["gripper"]),
            objects={k: ObjectEst.from_dict(v) for k, v in d["objects"].items()},
            confidence=dict(d.get("confidence", {})),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @staticmethod
    def from_json(s: str) -> "BeliefState":
        return BeliefState.from_dict(json.loads(s))


# --------------------------------------------------------------------------- #
# Predicates (task preconditions / effects)
# --------------------------------------------------------------------------- #
def pred_parts(p: str) -> tuple:
    """'in(red_cube,bin)' -> ('in', ('red_cube', 'bin'))."""
    head, _, rest = p.partition("(")
    args = tuple(a.strip() for a in rest.rstrip(")").split(",")) if rest else ()
    return head.strip(), args


def holding_pred(obj: str) -> str:
    return f"holding({obj})"


def in_pred(obj: str, region: str) -> str:
    return f"in({obj},{region})"


def on_pred(a: str, b: str) -> str:
    return f"on({a},{b})"


# --------------------------------------------------------------------------- #
# Forward-model prediction
# --------------------------------------------------------------------------- #
@dataclass
class Prediction:
    """Expected effects of a skill call (spec 6.1).

    expected_poses:   var -> (mean (4,), cov_diag (4,))
    expected_discrete: predicate var -> (expected bool, confidence)
    """

    expected_poses: dict = field(default_factory=dict)
    expected_discrete: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Diagnosis (spec 6.3)
# --------------------------------------------------------------------------- #
def _entropy(posterior: dict) -> float:
    return float(-sum(p * math.log(p) for p in posterior.values() if p > 0.0))


def summarize_posterior(posterior: dict) -> dict:
    ranked = sorted(posterior.items(), key=lambda kv: kv[1], reverse=True)
    p1, p2 = ranked[0][1], (ranked[1][1] if len(ranked) > 1 else 0.0)
    return {
        "argmax": ranked[0][0],
        "entropy": _entropy(posterior),
        "top2_gap": float(p1 - p2),
    }


@dataclass
class Diagnosis:
    posterior: dict                       # fault -> P(fault | evidence)
    argmax: str
    entropy: float                        # nats
    top2_gap: float
    evidence: dict = field(default_factory=dict)
    probes: list = field(default_factory=list)   # [(probe_name, outcome)]

    @staticmethod
    def from_posterior(posterior: dict, evidence: dict | None = None,
                       probes: list | None = None) -> "Diagnosis":
        s = summarize_posterior(posterior)
        return Diagnosis(
            posterior=dict(posterior), evidence=dict(evidence or {}),
            probes=list(probes or []), **s,
        )

    def to_dict(self) -> dict:
        return {
            "posterior": dict(self.posterior), "argmax": self.argmax,
            "entropy": self.entropy, "top2_gap": self.top2_gap,
            "evidence": dict(self.evidence), "probes": list(self.probes),
        }

    @staticmethod
    def from_dict(d: dict) -> "Diagnosis":
        return Diagnosis(
            posterior=d["posterior"], argmax=d["argmax"], entropy=d["entropy"],
            top2_gap=d["top2_gap"], evidence=d.get("evidence", {}),
            probes=[tuple(p) for p in d.get("probes", [])],
        )


# --------------------------------------------------------------------------- #
# Ledger entry (spec 5.2)
# --------------------------------------------------------------------------- #
@dataclass
class LedgerEntry:
    episode_id: str
    step: int
    subgoal: str
    skill: str
    preconditions: dict          # pred -> {"predicted": bool, "conf": float}
    expected_effects: dict       # var -> {"mean": [...], "cov_diag": [...]}
    predicted_state_conf: float
    observed_state: dict         # var -> {"mean": [...], "cov_diag": [...]}
    actual_effects: list
    divergence_D: float
    divergence_flagged: bool
    downstream: list             # subgoal ids affected below this step
    timestamp: float
    fault_free: bool = True      # no injected fault active at this step (metrics)
    evidence: dict = field(default_factory=dict)   # spec 6.3 features (M5 learning)
    plan_index: int = -1         # index in the topological plan (fault join)

    def to_dict(self) -> dict:
        return {
            "episode_id": self.episode_id, "step": self.step,
            "subgoal": self.subgoal, "skill": self.skill,
            "preconditions": self.preconditions,
            "expected_effects": self.expected_effects,
            "predicted_state_conf": self.predicted_state_conf,
            "observed_state": self.observed_state,
            "actual_effects": self.actual_effects,
            "divergence_D": self.divergence_D,
            "divergence_flagged": self.divergence_flagged,
            "downstream": list(self.downstream),
            "timestamp": self.timestamp,
            "fault_free": self.fault_free,
            "evidence": dict(self.evidence),
            "plan_index": int(self.plan_index),
        }

    @staticmethod
    def from_dict(d: dict) -> "LedgerEntry":
        return LedgerEntry(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @staticmethod
    def from_json(s: str) -> "LedgerEntry":
        return LedgerEntry.from_dict(json.loads(s))


# --------------------------------------------------------------------------- #
# Safety (spec 6.8)
# --------------------------------------------------------------------------- #
@dataclass
class SafetyVerdict:
    accepted: bool
    reason: str
    violated: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"accepted": self.accepted, "reason": self.reason,
                "violated": list(self.violated)}


# --------------------------------------------------------------------------- #
# Recovery decision (spec 6.4-6.6, 9)
# --------------------------------------------------------------------------- #
@dataclass
class RecoveryDecision:
    chosen: str                     # recovery type or "none" / "escalate"
    params: dict = field(default_factory=dict)
    cost_terms: dict = field(default_factory=dict)
    feasible: list = field(default_factory=list)   # [(type, expected_cost)]
    escalate: bool = False
    report: dict | None = None      # Section 9 structured report on escalation

    def to_dict(self) -> dict:
        return {
            "chosen": self.chosen, "params": self.params,
            "cost_terms": self.cost_terms, "feasible": self.feasible,
            "escalate": self.escalate, "report": self.report,
        }

    @staticmethod
    def from_dict(d: dict) -> "RecoveryDecision":
        return RecoveryDecision(**d)


# --------------------------------------------------------------------------- #
# Skill execution result (sim / ROS bridge)
# --------------------------------------------------------------------------- #
@dataclass
class SkillResult:
    status: str                     # ok | timeout | abort
    telemetry: dict = field(default_factory=dict)
    # telemetry keys: force_profile (list[(t, N)]), gripper_width,
    # grasp_attempted, grasp_offset, ee_path, ee_end_error, events (list[str])

    def to_dict(self) -> dict:
        return {"status": self.status, "telemetry": self.telemetry}
