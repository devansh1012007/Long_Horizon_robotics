"""Recovery candidate generation, affordances and cost-based selection
(spec 6.4-6.6), plus the Section 9 escalation report.
"""
from __future__ import annotations

from .diagnosis import FAULTS
from .types import RecoveryDecision

# Recovery macros. Each type is executed by the executor/pipeline as a compound
# action (e.g. regrasp = re-observe + pick at the observed pose).
RECOVERY_TYPES = [
    "regrasp", "reobserve_retry_modified", "retry_modified",
    "reobserve_redetect", "correction_move", "regrasp_replace",
    "reobserve_replan", "rollback", "replan_motion", "escalate",
]

# Base time (s) and extra-action counts, profiled nominally (spec 6.6 table).
RECOVERY_SPECS = {
    "regrasp":                 {"T": 14.0, "C": 2},
    "reobserve_retry_modified": {"T": 12.0, "C": 2},
    "retry_modified":          {"T": 8.0,  "C": 1},
    "reobserve_redetect":      {"T": 5.0,  "C": 1},
    "correction_move":         {"T": 6.0,  "C": 1},
    "regrasp_replace":         {"T": 18.0, "C": 3},
    "reobserve_replan":        {"T": 12.0, "C": 2},
    "rollback":                {"T": 30.0, "C": 2},
    "replan_motion":           {"T": 15.0, "C": 1},
    "escalate":                {"T": 45.0, "C": 1},
}

# Affordance sets per fault (spec 6.4). Plain retry of the same grasp is NOT
# an affordance of f_miss ("plain retry of the same grasp is disallowed until
# cause resolved"); escalate is always appended for any fault.
AFFORDANCES = {
    "f_none":       ["reobserve_redetect"],
    "f_miss":       ["regrasp", "reobserve_retry_modified"],
    "f_slip":       ["reobserve_redetect", "regrasp", "retry_modified", "rollback"],
    "f_pose":       ["correction_move", "regrasp_replace"],
    "f_collision":  ["reobserve_replan", "rollback", "replan_motion"],
    "f_perception": ["reobserve_redetect"],
    "f_controller": ["retry_modified", "replan_motion"],
}

# P(fail | recovery, fault): hand-set v1 table (spec 6.6), calibrated later.
PFAIL = {
    "regrasp":                  {"f_none": 0.05, "f_miss": 0.15, "f_slip": 0.20, "f_pose": 0.15, "f_collision": 0.35, "f_perception": 0.45, "f_controller": 0.35},
    "reobserve_retry_modified": {"f_none": 0.08, "f_miss": 0.20, "f_slip": 0.18, "f_pose": 0.20, "f_collision": 0.30, "f_perception": 0.40, "f_controller": 0.25},
    "retry_modified":           {"f_none": 0.15, "f_miss": 0.60, "f_slip": 0.30, "f_pose": 0.40, "f_collision": 0.55, "f_perception": 0.60, "f_controller": 0.20},
    "reobserve_redetect":       {"f_none": 0.20, "f_miss": 0.65, "f_slip": 0.60, "f_pose": 0.60, "f_collision": 0.60, "f_perception": 0.12, "f_controller": 0.55},
    "correction_move":          {"f_none": 0.20, "f_miss": 0.70, "f_slip": 0.50, "f_pose": 0.10, "f_collision": 0.60, "f_perception": 0.65, "f_controller": 0.50},
    "regrasp_replace":          {"f_none": 0.08, "f_miss": 0.18, "f_slip": 0.18, "f_pose": 0.12, "f_collision": 0.40, "f_perception": 0.50, "f_controller": 0.40},
    "reobserve_replan":         {"f_none": 0.10, "f_miss": 0.25, "f_slip": 0.25, "f_pose": 0.25, "f_collision": 0.25, "f_perception": 0.20, "f_controller": 0.25},
    "rollback":                 {"f_none": 0.15, "f_miss": 0.30, "f_slip": 0.22, "f_pose": 0.25, "f_collision": 0.45, "f_perception": 0.60, "f_controller": 0.45},
    "replan_motion":            {"f_none": 0.12, "f_miss": 0.55, "f_slip": 0.45, "f_pose": 0.45, "f_collision": 0.50, "f_perception": 0.55, "f_controller": 0.15},
    "escalate":                 {f: 0.0 for f in FAULTS},
}

# P(unsafe | recovery, fault): rollback after a collision is risky (scene
# disturbed); the safety supervisor's epsilon filter vetoes it (spec 6.4
# "rollback only if safe").
RISK = {
    "regrasp":                  {"f_none": 0.005, "f_miss": 0.005, "f_slip": 0.005, "f_pose": 0.005, "f_collision": 0.008, "f_perception": 0.005, "f_controller": 0.005},
    "reobserve_retry_modified": {"f_none": 0.006, "f_miss": 0.006, "f_slip": 0.006, "f_pose": 0.006, "f_collision": 0.008, "f_perception": 0.006, "f_controller": 0.006},
    "retry_modified":           {"f_none": 0.005, "f_miss": 0.005, "f_slip": 0.005, "f_pose": 0.005, "f_collision": 0.010, "f_perception": 0.005, "f_controller": 0.005},
    "reobserve_redetect":       {f: 0.0 for f in FAULTS},
    "correction_move":          {"f_none": 0.008, "f_miss": 0.008, "f_slip": 0.008, "f_pose": 0.008, "f_collision": 0.012, "f_perception": 0.008, "f_controller": 0.008},
    "regrasp_replace":          {"f_none": 0.010, "f_miss": 0.010, "f_slip": 0.010, "f_pose": 0.010, "f_collision": 0.015, "f_perception": 0.010, "f_controller": 0.010},
    "reobserve_replan":         {"f_none": 0.003, "f_miss": 0.003, "f_slip": 0.003, "f_pose": 0.003, "f_collision": 0.004, "f_perception": 0.003, "f_controller": 0.003},
    "rollback":                 {"f_none": 0.005, "f_miss": 0.010, "f_slip": 0.008, "f_pose": 0.006, "f_collision": 0.050, "f_perception": 0.005, "f_controller": 0.005},
    "replan_motion":            {"f_none": 0.006, "f_miss": 0.006, "f_slip": 0.006, "f_pose": 0.006, "f_collision": 0.008, "f_perception": 0.006, "f_controller": 0.006},
    "escalate":                 {f: 0.0 for f in FAULTS},
}

WEIGHTS = {"alpha": 1.0, "beta": 0.5, "gamma": 20.0, "delta": 60.0,
           "epsilon": 0.01}  # spec 6.6 defaults

FAILURE_PHRASES = {
    "f_miss": "Grasp missed the object",
    "f_slip": "Object slipped during transport",
    "f_pose": "Placement error: object landed off target",
    "f_collision": "Unexpected contact displaced an object",
    "f_perception": "Perception unreliable: object weakly visible",
    "f_controller": "Controller timeout/abort during motion",
    "f_none": "Unexplained divergence",
}


def record_recovery_failure(loop_guard: dict, subgoal: str, argmax: str,
                            rtype: str) -> None:
    """Loop guard (spec 6.6): after two failed recoveries of the same type for
    the same fault, that type is removed from R_feasible for the subgoal."""
    key = (subgoal, argmax, rtype)
    loop_guard[key] = loop_guard.get(key, 0) + 1


def choose_recovery(diagnosis, subgoal_id: str, loop_guard: dict | None = None,
                    scale: float = 1.0, evidence: list | None = None,
                    stale_subgoals: list | None = None,
                    weights: dict | None = None,
                    pfail: dict | None = None,
                    risk: dict | None = None) -> RecoveryDecision:
    """r* = argmin_r alpha*T(r) + beta*C(r) + gamma*E_f[P(fail|r,f)] + delta*H(r)
    s.t. E_f[P(unsafe|r,f)] <= epsilon (hard), r in affordances of plausible
    faults, r not loop-guarded.
    pfail/risk: optional learned tables (M5 affordance updates); defaults to
    the module tables (hand-set v1, spec 6.6)."""
    w = dict(WEIGHTS)
    w.update(weights or {})
    PFAIL_T = pfail if pfail is not None else PFAIL
    RISK_T = risk if risk is not None else RISK
    loop_guard = loop_guard or {}
    post = diagnosis.posterior
    plausible = [f for f in FAULTS if post.get(f, 0.0) >= 0.05 and f != "f_none"] \
        or [diagnosis.argmax]

    candidates: set = set()
    for f in plausible:
        candidates.update(AFFORDANCES.get(f, []))
    candidates.add("escalate")

    feasible = []
    for r in sorted(candidates):
        if loop_guard.get((subgoal_id, diagnosis.argmax, r), 0) >= 2:
            continue  # loop guard removes repeated identical recoveries
        if r == "escalate":
            continue  # handled as the fallback below
        risk = sum(post[f] * RISK_T[r][f] for f in FAULTS)
        if risk > w["epsilon"]:
            continue  # hard safety constraint
        pfail_e = sum(post[f] * PFAIL_T[r][f] for f in FAULTS)
        cost = (w["alpha"] * RECOVERY_SPECS[r]["T"] * scale
                + w["beta"] * RECOVERY_SPECS[r]["C"]
                + w["gamma"] * pfail_e)
        feasible.append((r, round(cost, 3), risk, pfail_e))

    esc_cost = (w["alpha"] * RECOVERY_SPECS["escalate"]["T"] * scale
                + w["beta"] * RECOVERY_SPECS["escalate"]["C"] + w["delta"])
    feasible_sorted = sorted(feasible, key=lambda t: t[1])
    if not feasible_sorted or feasible_sorted[0][1] > esc_cost:
        alts = [{"action": r, "time_s": round(RECOVERY_SPECS[r]["T"] * scale, 1),
                 "risk": round(risk, 3), "expected_cost": c}
                for r, c, risk, _pf in feasible_sorted[:3]]
        report = {
            "failure_reason":
                f"{FAILURE_PHRASES[diagnosis.argmax]} (p={post[diagnosis.argmax]:.2f})",
            "evidence": list(evidence or []),
            "alternatives": alts,
            "stale_subgoals": list(stale_subgoals or []),
            "why_escalating": (
                "all safe options exceed autonomy risk threshold"
                if not feasible_sorted else
                "all safe options exceed escalation cost"),
        }
        return RecoveryDecision(chosen="escalate", cost_terms={"cost": round(esc_cost, 3)},
                                feasible=alts, escalate=True, report=report)

    r, cost, risk, pfail = feasible_sorted[0]
    return RecoveryDecision(
        chosen=r,
        cost_terms={"T": RECOVERY_SPECS[r]["T"] * scale, "C": RECOVERY_SPECS[r]["C"],
                    "P_fail": round(pfail, 4), "P_unsafe": round(risk, 4),
                    "cost": cost},
        feasible=[(t[0], t[1]) for t in feasible_sorted],
        escalate=False,
    )
