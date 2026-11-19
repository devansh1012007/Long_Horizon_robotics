"""Perception noise model (spec 8): Gaussian pose noise, dropout, occlusion.
Ground-truth poses + noise model are used for most experiments; a
perception-realism ablation (A6) raises the noise/dropout rates.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from rg_core.types import BeliefState, ObjectEst

from .world import World


@dataclass
class NoiseCfg:
    pose_sigma: float = 0.008       # metres
    yaw_sigma: float = 0.05         # rad
    dropout: float = 0.02           # per-object detection dropout
    ee_sigma: float = 0.004         # EE telemetry noise
    occl_cov_mult: float = 8.0      # covariance inflation under partial view


def observe(world: World, rng: np.random.Generator, cfg: NoiseCfg,
            occluded: set | None = None) -> BeliefState:
    """One camera frame -> belief over objects, EE and gripper. Occluded
    objects are reported invisible (no pose estimate) - matching the
    f_perception mechanism of a masked camera region (spec 10.1)."""
    occluded = occluded or set()
    ests = {}
    for name, o in world.objects.items():
        if o.is_region:
            # regions are static and calibrated via AprilTags: near-perfect
            ests[name] = ObjectEst(name, o.pose.copy(), np.full(4, 1e-6))
            continue
        if name in occluded or rng.random() < cfg.dropout:
            est = ObjectEst(name, o.pose.copy(), np.full(4, 1e6),
                            visible=False)
        else:
            mean = o.pose + rng.normal(0.0, [cfg.pose_sigma, cfg.pose_sigma,
                                             cfg.pose_sigma, cfg.yaw_sigma])
            cov = np.array([cfg.pose_sigma**2] * 3 + [cfg.yaw_sigma**2])
            est = ObjectEst(name, mean, cov, visible=True)
        ests[name] = est
    ee_mean = world.ee + rng.normal(0, cfg.ee_sigma, 4)
    grip = world.gripper
    g_obs = type(grip)(grip.state, max(0.0, grip.width + rng.normal(0, 0.002)),
                       max(0.0, grip.force + rng.normal(0, 0.5)),
                       grip.commanded_force)
    conf = {}
    for name, est in ests.items():
        # regions are static/calibrated; visibility confidence only for objects
        if name not in ("bin", "plate"):
            conf[f"visible({name})"] = 1.0 if est.visible else 0.0
    if world.held is not None:
        conf[f"holding({world.held})"] = 0.95
    return BeliefState(
        t=world.sim_t, ee_pose=ee_mean,
        ee_cov_diag=np.array([cfg.ee_sigma**2] * 3 + [(2 * cfg.ee_sigma)**2]),
        gripper=g_obs, objects=ests, confidence=conf)
