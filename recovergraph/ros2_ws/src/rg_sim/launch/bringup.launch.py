"""Bringup launch (spec Section 7): starts all RecoverGraph nodes.

  ros2 launch rg_sim bringup.launch.py

Note: this scaffold is validated structurally (see scripts/check_ros_scaffold.py);
end-to-end behaviour is exercised on a machine with ROS 2 Jazzy installed
(docker/Dockerfile.ros).
"""
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(package="rg_task", executable="task_parser", output="screen"),
        Node(package="rg_task", executable="task_planner", output="screen"),
        Node(package="rg_perception", executable="scene_observer",
             output="screen"),
        Node(package="rg_perception", executable="state_estimator",
             output="screen"),
        Node(package="rg_exec", executable="skill_executor", output="screen"),
        # spec 6.8: the supervisor runs as its own process
        Node(package="rg_exec", executable="safety_supervisor",
             output="screen"),
        Node(package="rg_recovery", executable="effect_verifier",
             output="screen"),
        Node(package="rg_recovery", executable="fault_diagnoser",
             output="screen"),
        Node(package="rg_recovery", executable="recovery_planner",
             output="screen"),
        Node(package="rg_sim", executable="sim_bridge", output="screen"),
        Node(package="rg_sim", executable="fault_injector", output="screen"),
    ])
