"""In-process message-bus shim for ROS 2 Jazzy (M4 bring-up in the sandbox).

The six rg_* packages run their unmodified node code over this bus when no
ROS 2 installation is available: `rclpy`, `rg_msgs`, `sensor_msgs` and
`std_msgs` resolve to shim packages with the same import paths and API
surface the nodes use (Node/Publisher/Subscription/Service/Client/Timer/
ActionServer/parameters/clock/logger). Topic delivery round-trips every
message through a dict representation (wire fidelity + no mutable aliasing)
and records per-message latency, which is the ADR-003 telemetry source.

The same node files run unmodified under real rclpy in the Docker images;
this shim is the sandbox bring-up path, not a substitute for ros2 CI.

Usage:
    from shim import install      # ros2_ws/shim
    install()                     # inserts shim dir into sys.path if needed
    import rclpy                  # resolves to the shim (or real rclpy)
"""
from __future__ import annotations

import os
import sys

_SHIM_DIR = os.path.dirname(os.path.abspath(__file__))


def install() -> bool:
    """Make the shim importable; returns True when the shim is in use."""
    try:
        import rclpy  # noqa: F401
        return False                       # real ROS 2 present
    except ImportError:
        if _SHIM_DIR not in sys.path:
            sys.path.insert(0, _SHIM_DIR)
        return True


__all__ = ["install"]
