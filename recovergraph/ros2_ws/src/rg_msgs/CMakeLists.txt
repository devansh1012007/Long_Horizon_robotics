cmake_minimum_required(VERSION 3.8)
project(rg_msgs)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/TaskGoal.msg"
  "msg/TaskGraphMsg.msg"
  "msg/ObjectEstimate.msg"
  "msg/ObjectEstimateArray.msg"
  "msg/BeliefState.msg"
  "msg/LedgerEntry.msg"
  "msg/ExecEvent.msg"
  "msg/FaultPosterior.msg"
  "msg/RecoveryDecisionMsg.msg"
  "msg/SafetyVerdict.msg"
  "msg/EscalationReport.msg"
  "msg/GripperState.msg"
  "srv/ParseInstruction.srv"
  "srv/Replan.srv"
  "srv/Reobserve.srv"
  "srv/VerifyEffect.srv"
  "srv/ProbeSuggestion.srv"
  "srv/CheckTrajectory.srv"
  "srv/ExecuteSkill.srv"
  "action/SkillExecute.action"
  "action/RecoveryExecute.action"
  DEPENDENCIES std_msgs builtin_interfaces
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
