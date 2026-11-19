"""rg_msgs.srv shim: the 7 services (field parity with the .srv files)."""
from msgbase import make_srv
from ..msg import (FaultPosterior, LedgerEntry, ObjectEstimateArray,
                  SafetyVerdict, TaskGraphMsg)

CheckTrajectory = make_srv("CheckTrajectory", [
    ("trajectory_json", str), ("held_object", str),
], [
    ("verdict", ("msg", SafetyVerdict)),
])

ExecuteSkill = make_srv("ExecuteSkill", [
    ("episode_id", str), ("skill", str), ("args_json", str),
    ("params_json", str),
], [
    ("success", bool), ("status", str), ("telemetry_json", str),
])

ParseInstruction = make_srv("ParseInstruction", [
    ("instruction", str),
], [
    ("graph", ("msg", TaskGraphMsg)), ("error", str),
])

ProbeSuggestion = make_srv("ProbeSuggestion", [
    ("posterior", ("msg", FaultPosterior)),
], [
    ("probe_type", str), ("params_json", str),
    ("expected_information_gain", float), ("probe_needed", bool),
])

Reobserve = make_srv("Reobserve", [
    ("viewpoint_hint", str),
], [
    ("scene", ("msg", ObjectEstimateArray)), ("success", bool),
])

Replan = make_srv("Replan", [
    ("stale_subgoals", list), ("corrupted_vars", list),
    ("current_graph", ("msg", TaskGraphMsg)),
], [
    ("patched_graph", ("msg", TaskGraphMsg)), ("success", bool),
    ("error", str),
])

VerifyEffect = make_srv("VerifyEffect", [
    ("entry", ("msg", LedgerEntry)),
], [
    ("verified", bool), ("confidence", float), ("reason", str),
])

__all__ = [name for name in dir() if not name.startswith("_") and
           name[0].isupper()]
