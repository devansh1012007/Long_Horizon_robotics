"""Fault injection (spec 10.1): 7 fault types, sampled parameters, stratified
injection step, logged seed. Ground-truth labels follow the injected
mechanism; object displacement is labelled f_collision (displaced) and
precondition flips are labelled stale_dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from rg_core.types import INJECTED_LABELS

# Diagnosable fault types (the Bayes diagnoser's hypothesis space minus f_none)
DIAGNOSABLE = ["f_miss", "f_slip", "f_pose", "f_collision",
               "f_perception", "f_controller"]

# Which fault mechanisms are physically meaningful per skill tag: a slip
# needs a transport motion, a grasp offset needs a grasp, a release
# perturbation needs a release (spec 10.1 injection column). The pipeline
# tags move steps as move_transport (carrying an object) or move_free.
SKILL_FAULTS = {
    "pick": ["f_miss", "f_perception", "f_controller", "stale_dependency"],
    "move_transport": ["f_slip", "f_collision", "f_perception",
                       "f_controller", "stale_dependency"],
    "move_free": ["f_collision", "f_perception", "f_controller",
                  "stale_dependency"],
    "place": ["f_pose", "f_perception", "f_controller", "stale_dependency"],
}


@dataclass
class FaultEvent:
    step: int                       # subgoal index (0-based) where it fires
    fault: str
    params: dict = field(default_factory=dict)
    label: str = ""                 # ground-truth label (spec 10.1)

    def __post_init__(self):
        if not self.label:
            self.label = self.fault

    def to_dict(self) -> dict:
        return {"step": self.step, "fault": self.fault,
                "params": self.params, "label": self.label}


PARAM_RANGES = {
    "f_miss": {"offset": (0.02, 0.06)},                       # 2-6 cm lateral
    "f_slip": {"friction_mult": (0.3, 0.6), "pulse": (5.0, 15.0),
               "dur_ms": (50.0, 150.0)},
    "f_pose": {"pos": (0.02, 0.08), "yaw_deg": (5.0, 20.0)},
    "f_perception": {"occl_frac": (0.3, 0.8), "dur_s": (1.0, 3.0)},
    "f_collision": {"shift": (0.03, 0.15)},                   # displaced
    "f_controller": {},
    "stale_dependency": {"shift": (0.03, 0.15)},
}


def sample_fault_params(rng: np.random.Generator, fault: str) -> dict:
    return {k: float(rng.uniform(*v)) for k, v in PARAM_RANGES[fault].items()}


def sample_schedule(rng: np.random.Generator, n_subgoals: int,
                    faults: list | tuple | None = None,
                    n_faults: int = 1, stratify: str = "random",
                    skills: list | None = None) -> list:
    """Sample a fault schedule. stratify in {random, early, middle, late}
    (spec 10.1: injection at a random step, stratified early/middle/late).
    Compound episodes use two faults at distinct steps (stress test).
    `skills` (one skill name per subgoal) restricts each fault to mechanisms
    that are physically meaningful for that step's skill.
    A single pinned fault (len(faults) == 1 and n_faults == 1) is injected
    only on a compatible step; if the plan has none, no fault is injected
    (the mechanism is structurally impossible for that task and the episode
    is reported fault-free - callers skip it from fault-cell statistics)."""
    faults = list(faults) if faults else DIAGNOSABLE + ["stale_dependency"]
    pinned = faults and len(faults) == 1 and n_faults == 1
    if stratify == "early":
        lo, hi = 0, max(1, n_subgoals // 3)
    elif stratify == "middle":
        lo, hi = n_subgoals // 3, max(2, 2 * n_subgoals // 3)
    elif stratify == "late":
        lo, hi = 2 * n_subgoals // 3, n_subgoals
    else:
        lo, hi = 0, n_subgoals
    hi = max(lo + 1, hi)
    steps = list(rng.choice(np.arange(lo, hi), size=min(n_faults, hi - lo),
                            replace=False))
    if pinned and skills is not None:
        compat = [i for i in range(n_subgoals)
                  if faults[0] in SKILL_FAULTS.get(skills[i], [])]
        if not compat:
            return []            # mechanism structurally impossible here
        steps = [int(rng.choice(compat))][:min(n_faults, len(compat))]
    events = []
    for step in steps:
        allowed = [f for f in faults
                   if skills is None or f in SKILL_FAULTS.get(skills[step],
                                                              faults)]
        if not allowed:
            allowed = faults
        fault = str(rng.choice(allowed))
        events.append(FaultEvent(int(step), fault, sample_fault_params(rng, fault)))
    return sorted(events, key=lambda e: e.step)


def episode_schedule(seed: int, n_subgoals: int, condition: str,
                     stratify: str = "random",
                     skills: list | None = None,
                     faults: list | None = None) -> list:
    """Condition in {none, single, compound, all}. Deterministic given seed.
    `faults` pins the injectable fault set (e.g. a single fault type for a
    pre-registered cell); labels still follow the injected mechanism."""
    rng = np.random.default_rng(seed)
    if condition == "none":
        return []
    if condition == "single":
        return sample_schedule(rng, n_subgoals, faults, 1, stratify, skills)
    if condition == "compound":
        return sample_schedule(rng, n_subgoals,
                               faults if faults else DIAGNOSABLE, 2, stratify,
                               skills)
    raise ValueError(condition)


def victim_object(task, world, subgoal_idx: int) -> str | None:
    """Pick a plausible displacement victim for f_collision at a step: the
    subgoal's own target object if held-free, else another cube."""
    subs = task.topo_order()
    sub = task.subgoal(subs[subgoal_idx])
    candidates = []
    if sub.skill == "pick" and "obj" in sub.args:
        candidates.append(sub.args["obj"])
    others = [n for n, o in world.objects.items()
              if not o.is_region and n != sub.args.get("obj")
              and n != world.held]
    return (candidates or others or [None])[0]
