"""rg_msgs.msg shim: the 12 interfaces of ros2_ws/src/rg_msgs (field parity)."""
from msgbase import make_msg
from std_msgs.msg import Header

ObjectEstimate = make_msg("ObjectEstimate", [
    ("name", str), ("pose_mean", list), ("pose_cov_diag", list),
    ("identity_conf", float), ("visible", bool)])

ObjectEstimateArray = make_msg("ObjectEstimateArray", [
    ("header", ("msg", Header)),
    ("objects", ("msg[]", ObjectEstimate))])

GripperState = make_msg("GripperState", [
    ("header", ("msg", Header)), ("state", str), ("width", float),
    ("force", float), ("commanded_force", float)])

BeliefState = make_msg("BeliefState", [
    ("header", ("msg", Header)),
    ("ee_pose", list), ("ee_cov_diag", list),
    ("gripper_state", str), ("gripper_width", float),
    ("gripper_force", float), ("gripper_commanded_force", float),
    ("objects", ("msg[]", ObjectEstimate)),
    ("confidence_json", str), ("belief_json", str)])

ExecEvent = make_msg("ExecEvent", [
    ("episode_id", str), ("t", float), ("node", str), ("type", str),
    ("payload_json", str)])

FaultPosterior = make_msg("FaultPosterior", [
    ("hypotheses", list), ("posterior", list), ("argmax", str),
    ("entropy", float), ("top2_gap", float),
    ("evidence_json", str), ("probes_json", str), ("posterior_json", str)])

LedgerEntry = make_msg("LedgerEntry", [
    ("episode_id", str), ("step", int), ("subgoal", str), ("skill", str),
    ("preconditions_json", str), ("expected_effects_json", str),
    ("predicted_state_conf", float), ("observed_state_json", str),
    ("actual_effects_json", str),
    ("divergence_d", float), ("divergence_flagged", bool),
    ("downstream", list), ("timestamp", float), ("fault_free", bool),
    ("entry_json", str)])

RecoveryDecisionMsg = make_msg("RecoveryDecisionMsg", [
    ("chosen", str), ("params_json", str), ("cost_terms_json", str),
    ("feasible_json", str), ("escalate", bool),
    ("escalation_report_json", str)])

SafetyVerdict = make_msg("SafetyVerdict", [
    ("accepted", bool), ("reason", str), ("violated", list)])

TaskGoal = make_msg("TaskGoal", [("goal_text", str), ("episode_id", str)])

TaskGraphMsg = make_msg("TaskGraphMsg", [
    ("task_id", str), ("goal", str), ("graph_json", str)])

EscalationReport = make_msg("EscalationReport", [
    ("failure_reason", str), ("evidence", list),
    ("alternatives_json", str), ("stale_subgoals", list),
    ("why_escalating", str)])

__all__ = [name for name in dir() if not name.startswith("_") and
           name[0].isupper()]
