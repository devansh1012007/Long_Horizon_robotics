"""rg_msgs.action shim: the 2 actions (goal/result/feedback parity)."""
from msgbase import make_action
from ..msg import RecoveryDecisionMsg

SkillExecute = make_action("SkillExecute", [
    ("episode_id", str), ("skill", str), ("args_json", str),
    ("params_json", str),
], [
    ("success", bool), ("status", str), ("telemetry_json", str),
    ("effects_json", str),
], [
    ("progress", float), ("phase", str),
])

RecoveryExecute = make_action("RecoveryExecute", [
    ("episode_id", str), ("decision", ("msg", RecoveryDecisionMsg)),
], [
    ("success", bool), ("new_diagnosis_json", str),
    ("restored_subgoals_json", str),
], [
    ("progress", float), ("phase", str),
])

__all__ = ["SkillExecute", "RecoveryExecute"]
