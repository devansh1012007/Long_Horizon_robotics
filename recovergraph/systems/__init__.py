"""System adapters (spec 10.3): identical skills/perception/planner, differing
only in divergence-handling logic. Systems never see the injected truth label.
"""
from __future__ import annotations

from rg_core.diagnosis import PROBES, diagnose, suggest_probe, update_with_probe
from rg_core.recovery import AFFORDANCES, RISK, choose_recovery
from rg_core.types import Diagnosis, FAULTS


class SystemAdapter:
    name = "base"
    point_estimates = False   # A3 sets True (detection without uncertainty)

    def handle_divergence(self, ctx: dict) -> dict:
        raise NotImplementedError


class B1NoRecovery(SystemAdapter):
    """B1: low-level skills without recovery."""
    name = "B1"

    def handle_divergence(self, ctx: dict) -> dict:
        return {"action": "none"}


class B2FixedRetry(SystemAdapter):
    """B2: fixed retry, up to 3 attempts (spec 10.3)."""
    name = "B2"

    def handle_divergence(self, ctx: dict) -> dict:
        if ctx["attempts"] < 3:
            return {"action": "retry", "params": {}}
        return {"action": "abort"}


class B3FullReplan(SystemAdapter):
    """B3: full replanning from the current observed state."""
    name = "B3"

    def handle_divergence(self, ctx: dict) -> dict:
        return {"action": "replanned"}


class B4VerificationOrchestrator(SystemAdapter):
    """B4: LADDER-style verification-based orchestration: hard effect
    verification per subgoal, re-observe + retry loop, no causal model, no
    dependency invalidation beyond precondition re-checks."""
    name = "B4"

    def handle_divergence(self, ctx: dict) -> dict:
        ctx["facade"].reobserve()
        if ctx["attempts"] < 3:
            return {"action": "retry", "params": {}}
        return {"action": "abort"}


class B5FaultPipeline(SystemAdapter):
    """B5: classic fault pipeline - pose/wrench threshold detection mapped to
    forward/backward recovery actions, deterministic, no posterior, no cost
    optimization (spec 10.3, reproducible variant)."""
    name = "B5"

    def handle_divergence(self, ctx: dict) -> dict:
        ev = ctx["evidence"]
        if ctx["attempts"] >= 3:
            return {"action": "abort"}
        if ev.get("gripper_closed_narrow"):
            rtype = "regrasp"                       # forward recovery
        elif ev.get("grip_force_drop"):
            rtype = "rollback"                      # backward then retry
        elif ev.get("tracked_obj_final_pose_off"):
            rtype = "correction_move"
        elif ev.get("controller_timeout_abort"):
            rtype = "retry_modified"
        elif ev.get("non_target_object_moved"):
            rtype = "reobserve_replan"
        elif ev.get("low_visibility_ee_nominal"):
            rtype = "reobserve_redetect"
        else:
            rtype = "retry_modified"
        return {"action": "recovery", "recovery_type": rtype}


class RecoverGraph(SystemAdapter):
    """RG: full pipeline - Bayes diagnosis with ambiguity-gated probes,
    dependency-aware invalidation, cost-minimising safe recovery, loop guard."""
    name = "RG"
    # learned-artifact tables (M5): None -> hand-set module tables
    diag_tables: dict | None = None
    pfail_table: dict | None = None

    def handle_divergence(self, ctx: dict) -> dict:
        diag = diagnose(ctx["evidence"], ctx["skill"],
                         tables=getattr(self, "diag_tables", None))
        probes_used = []
        # Spec 6.3 ambiguity gate; probing on a fully uninformative posterior
        # (no evidence at all) only burns actions, so it is skipped there.
        s = suggest_probe(diag) if any(ctx["evidence"].values()) else None
        if s is not None:
            probe_name, ig = s
            outcome = ctx["facade"].execute_probe(probe_name)
            post2 = update_with_probe(diag.posterior, PROBES[probe_name], outcome)
            probes_used = [(probe_name, outcome)]
            diag = Diagnosis.from_posterior(post2, evidence=ctx["evidence"],
                                            probes=probes_used)
        decision = choose_recovery(
            diag, ctx["subgoal_id"], loop_guard=ctx["loop_guard"],
            scale=ctx.get("scale", 1.0), evidence=ctx.get("notes"),
            stale_subgoals=ctx.get("stale"),
            pfail=getattr(self, "pfail_table", None),
            weights=getattr(self, "recovery_weights", None))
        if decision.escalate:
            return {"action": "escalate", "decision": decision,
                    "diagnosis": diag}
        resp = {"action": "recovery", "recovery_type": decision.chosen,
                "decision": decision, "diagnosis": diag}
        if ctx.get("kind") == "invariant" and ctx.get("repair_subgoal"):
            resp["repair_subgoal"] = ctx["repair_subgoal"]
        return resp


class A1NoDiagnosis(RecoverGraph):
    """A1: causal diagnosis replaced by plain verification (success/fail
    only): fixed-priority recovery without a posterior, no probes (spec 10.4)."""
    name = "A1"
    PRIORITY = ["regrasp", "retry_modified", "reobserve_redetect", "rollback"]

    def handle_divergence(self, ctx: dict) -> dict:
        lg = ctx["loop_guard"]
        for r in self.PRIORITY:
            if lg.get((ctx["subgoal_id"], "unknown", r), 0) >= 2:
                continue
            return {"action": "recovery", "recovery_type": r}
        return {"action": "abort"}


class A2NoInvalidation(RecoverGraph):
    """A2: dependency invalidation removed - everything replanned."""
    name = "A2"

    def handle_divergence(self, ctx: dict) -> dict:
        resp = super().handle_divergence(ctx)
        resp["invalidate"] = "all"
        return resp


class A3PointEstimates(RecoverGraph):
    """A3: uncertainty removed from detection (point estimates)."""
    name = "A3"
    point_estimates = True


class A4FixedPriority(RecoverGraph):
    """A4: cost-based selection replaced by a fixed priority ordering over
    the argmax fault's affordances (hard safety filter retained)."""
    name = "A4"

    def handle_divergence(self, ctx: dict) -> dict:
        diag = diagnose(ctx["evidence"], ctx["skill"])
        lg = ctx["loop_guard"]
        for r in AFFORDANCES.get(diag.argmax, []):
            if lg.get((ctx["subgoal_id"], diag.argmax, r), 0) >= 2:
                continue
            risk = RISK[r][diag.argmax]
            if risk > 0.01:
                continue
            return {"action": "recovery", "recovery_type": r}
        return {"action": "abort"}


class A5LearnedLikelihood(RecoverGraph):
    """A5: hand-set vs learned likelihoods. If a calibrated likelihood table
    exists (Phase 4b artifact), it is injected as this system's tables (no
    global mutation); otherwise the hand-set table is retained and the run
    is marked not-run (honest reporting)."""
    name = "A5"

    def __init__(self, artifact_path: str | None = None):
        import json
        import os
        self.learned_available = False
        if artifact_path and os.path.exists(artifact_path):
            with open(artifact_path) as fh:
                artifact = json.load(fh)
            if artifact.get("likelihoods"):
                # A5 per spec: swap the LIKELIHOODS only; priors stay hand-set
                self.diag_tables = {"likelihoods": artifact["likelihoods"]}
                self.learned_available = True


class RGLearned(RecoverGraph):
    """RG + full learning products (M5): learned likelihoods, skill priors
    and affordance-failure tables from the calibration artifact."""
    name = "RG-LEARNED"

    def __init__(self, artifact_path: str | None = None):
        import json
        import os
        self.learned_available = False
        if artifact_path and os.path.exists(artifact_path):
            with open(artifact_path) as fh:
                artifact = json.load(fh)
            self.diag_tables = {"likelihoods": artifact.get("likelihoods", {}),
                                "priors": artifact.get("skill_priors", {})}
            self.pfail_table = artifact.get("pfail")
            self.learned_available = bool(artifact.get("likelihoods"))


REGISTRY = {
    "B1": B1NoRecovery, "B2": B2FixedRetry, "B3": B3FullReplan,
    "B4": B4VerificationOrchestrator, "B5": B5FaultPipeline,
    "RG": RecoverGraph, "A1": A1NoDiagnosis, "A2": A2NoInvalidation,
    "A3": A3PointEstimates, "A4": A4FixedPriority, "A5": A5LearnedLikelihood,
    "RG-LEARNED": RGLearned,
}


def make_system(system_id: str, artifact_path: str | None = None):
    cls = REGISTRY[system_id]
    if system_id in ("A5", "RG-LEARNED"):
        return cls(artifact_path)
    return cls()
