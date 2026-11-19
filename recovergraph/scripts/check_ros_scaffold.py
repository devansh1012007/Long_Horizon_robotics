"""Structural validation of the ROS 2 scaffold (runs WITHOUT ROS installed).

Checks: expected files exist and are non-empty; msg/srv/action files end with
a newline (rosidl requirement); XML parses; setup.py entry points reference
existing modules; Python sources compile; imports of rg_core resolve.

Run: python scripts/check_ros_scaffold.py
"""
from __future__ import annotations

import ast
import os
import sys
import xml.etree.ElementTree as ET

WS = os.path.join(os.path.dirname(__file__), "..", "ros2_ws", "src")

EXPECTED_MSGS = ["TaskGoal", "TaskGraphMsg", "ObjectEstimate",
                 "ObjectEstimateArray", "BeliefState", "LedgerEntry",
                 "ExecEvent", "FaultPosterior", "RecoveryDecisionMsg",
                 "SafetyVerdict", "EscalationReport", "GripperState"]
EXPECTED_SRVS = ["ParseInstruction", "Replan", "Reobserve", "VerifyEffect",
                 "ProbeSuggestion", "CheckTrajectory", "ExecuteSkill"]
EXPECTED_ACTIONS = ["SkillExecute", "RecoveryExecute"]
PACKAGES = ["rg_msgs", "rg_task", "rg_exec", "rg_perception", "rg_recovery",
            "rg_sim"]

errors: list = []


def err(msg):
    errors.append(msg)


def check_msg_files():
    for kind, names in (("msg", EXPECTED_MSGS), ("srv", EXPECTED_SRVS),
                        ("action", EXPECTED_ACTIONS)):
        for name in names:
            path = os.path.join(WS, "rg_msgs", kind, f"{name}.{kind.rstrip('s') if kind=='action' else kind}")
            path = os.path.join(WS, "rg_msgs", kind,
                                f"{name}.{'action' if kind == 'action' else kind}")
            if not os.path.exists(path):
                err(f"missing {path}")
                continue
            content = open(path).read()
            if not content.endswith("\n"):
                err(f"{path} must end with a newline (rosidl requirement)")
            if len(content.strip()) < 5:
                err(f"{path} suspiciously small")


def check_packages():
    for pkg in PACKAGES:
        pxml = os.path.join(WS, pkg, "package.xml")
        if not os.path.exists(pxml):
            err(f"missing {pxml}")
            continue
        try:
            root = ET.parse(pxml).getroot()
            name = root.find("name").text
            if name != pkg:
                err(f"{pxml}: <name>{name}</name> != {pkg}")
        except ET.ParseError as exc:
            err(f"{pxml}: XML parse error: {exc}")
        if pkg == "rg_msgs":
            if not os.path.exists(os.path.join(WS, pkg, "CMakeLists.txt")):
                err("rg_msgs missing CMakeLists.txt")
            continue
        for rel in ("setup.py", "setup.cfg"):
            if not os.path.exists(os.path.join(WS, pkg, rel)):
                err(f"{pkg} missing {rel}")
        moddir = os.path.join(WS, pkg, pkg)
        if not os.path.isdir(moddir):
            err(f"{pkg} missing module dir")
        setup_py = os.path.join(WS, pkg, "setup.py")
        if os.path.exists(setup_py):
            tree = ast.parse(open(setup_py).read())
            text = open(setup_py).read()
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and "=" in node.value and ":" in node.value:
                    mod = node.value.split("=")[-1].split(":")[0].strip()
                    mod_path = os.path.join(WS, pkg,
                                            mod.replace(".", "/") + ".py")
                    if not os.path.exists(mod_path):
                        err(f"{setup_py}: entry point module missing: {mod}")


def check_python_sources():
    for root, _dirs, files in os.walk(WS):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                try:
                    compile(open(path).read(), path, "exec")
                except SyntaxError as exc:
                    err(f"{path}: syntax error: {exc}")


def check_core_importable():
    sys.path.insert(0, os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..")))
    try:
        import rg_core  # noqa: F401
    except Exception as exc:  # pragma: no cover
        err(f"rg_core import failed: {exc}")


def main():
    check_msg_files()
    check_packages()
    check_python_sources()
    check_core_importable()
    if errors:
        print(f"FAIL ({len(errors)} problems):")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    n_msg = len(EXPECTED_MSGS) + len(EXPECTED_SRVS) + len(EXPECTED_ACTIONS)
    print(f"ROS 2 scaffold OK: {len(PACKAGES)} packages, "
          f"{n_msg} interfaces, all sources compile, package.xml valid.")


if __name__ == "__main__":
    main()
