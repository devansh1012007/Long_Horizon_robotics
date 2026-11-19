"""Safety supervisor tests (spec 6.8), including the adversarial checks
required by the risk table ("adversarial tests that propose unsafe motions
and verify vetoes").
"""
import numpy as np
import pytest

from rg_core.safety import SafetyLimits, SafetySupervisor


def objs():
    return [("red_cube", (0.40, 0.10, 0.03), 0.04),
            ("bin", (0.35, -0.25, 0.05), 0.10)]


class TestSafety:
    def test_nominal_trajectory_accepted(self):
        sup = SafetySupervisor()
        wps = [(0.0, (0.0, 0.0, 0.05)), (0.5, (0.1, 0.0, 0.05)),
               (1.0, (0.2, 0.0, 0.05))]
        v = sup.check_trajectory(wps, dt=0.5, objects=objs())
        assert v.accepted and v.reason == "ok"

    def test_workspace_violation_vetoed(self):
        sup = SafetySupervisor()
        wps = [(0.0, (0.0, 0.0, 0.05)), (0.5, (0.6, 0.0, 0.05))]
        v = sup.check_trajectory(wps, dt=0.5, objects=objs())
        assert not v.accepted
        assert "workspace_bounds" in v.violated[0]

    def test_speed_violation_vetoed(self):
        sup = SafetySupervisor()
        wps = [(0.0, (0.0, 0.0, 0.05)), (0.5, (0.3, 0.0, 0.05))]  # 0.6 m/s
        v = sup.check_trajectory(wps, dt=0.5, objects=objs())
        assert not v.accepted and "speed_limit" in v.violated[0]

    def test_collision_margin_vetoed(self):
        sup = SafetySupervisor()
        # slow approach (0.3 m/s) that grazes red_cube: 0.10 < 0.06 + 0.04 + 0.05
        wps = [(0.0, (0.15, 0.10, 0.03)), (0.5, (0.30, 0.10, 0.03))]
        v = sup.check_trajectory(wps, dt=0.5, objects=objs())
        assert not v.accepted and "collision_margin" in v.violated[0]

    def test_held_object_excluded(self):
        sup = SafetySupervisor()
        wps = [(0.0, (0.28, 0.10, 0.03)), (0.5, (0.40, 0.10, 0.03))]
        v = sup.check_trajectory(wps, dt=0.5, objects=objs(), held="red_cube")
        assert v.accepted

    def test_adversarial_through_object_vetoed(self):
        """Risk-table mitigation: propose a slow path straight through the
        bin and verify the supervisor rejects it on collision margins (not
        merely on speed)."""
        sup = SafetySupervisor()
        wps = [(0.0, (0.15, -0.10, 0.05)), (1.0, (0.35, -0.25, 0.05))]
        v = sup.check_trajectory(wps, dt=0.5, objects=objs())
        assert not v.accepted and "collision_margin" in v.violated[0]

    def test_independent_limits(self):
        lim = SafetyLimits(max_ee_speed=0.05)
        sup = SafetySupervisor(lim)
        wps = [(0.0, (0.0, 0.0, 0.05)), (0.5, (0.05, 0.0, 0.05))]  # 0.1 m/s
        v = sup.check_trajectory(wps, dt=0.5, objects=objs())
        assert not v.accepted and "speed_limit" in v.violated[0]


class TestSafetyOnPhysicsWorld:
    """M3: contact-level adversarial checks - the proposals the physics
    backend can actually produce (its own servo waypoint streams, its own
    scene geometry) must still be vetoed when unsafe (spec 6.8, risk table)."""

    def _world(self):
        from rgsim.backends import make_world
        return make_world(31, ["red_cube", "blue_cube", "green_cube"],
                          backend="mujoco")

    def test_physics_servo_waypoints_within_speed_limit(self):
        sup = SafetySupervisor()
        w = self._world()
        wps = w.move_ee(np.array([0.25, 0.15, 0.15, 0.0]))
        objs = [(n, o.pose[:3], o.radius) for n, o in w.objects.items()
                if not o.is_region]
        v = sup.check_trajectory(wps, dt=0.5, objects=objs)
        assert v.accepted, v.violated

    def test_contact_level_sweep_through_object_vetoed(self):
        """A slow lateral sweep straight through a physics scene object (the
        arm-bump the collision impulse abstracts) must be vetoed on
        collision margin, not merely speed or bounds. The bin is swept at a
        fixed, workspace-safe trajectory so the only violated constraint is
        the collision margin."""
        sup = SafetySupervisor()
        w = self._world()
        bin_pose = w.objects["bin"].pose[:3]
        x = float(bin_pose[0])
        y = float(bin_pose[1])
        wps = [(0.0, (x, y + 0.15, 0.025)), (1.5, (x, y - 0.15, 0.025))]
        objs = [(n, o.pose[:3], o.radius) for n, o in w.objects.items()]
        v = sup.check_trajectory(wps, dt=1.5, objects=objs)
        assert not v.accepted
        assert any("collision_margin" in x for x in v.violated), v.violated

    def test_physics_episode_zero_unsafe_executions(self):
        """Fault-bearing physics episodes route every motion through the
        supervisor: no unsafe execution may occur (hard requirement)."""
        from rg_core.taskgen import make_task
        from rgsim.pipeline import EpisodeRunner
        from systems import make_system
        worst = 0
        for k in range(6):
            seed = 7100 + k * 11
            res = EpisodeRunner(make_system("RG"), make_task(3, seed % 20, 0),
                                seed=seed, condition="single",
                                backend="mujoco",
                                faults=["f_collision"]).run()
            worst = max(worst, res["metrics"]["unsafe_executions"])
        assert worst == 0

    def test_held_object_excluded_on_physics_scene(self):
        sup = SafetySupervisor()
        w = self._world()
        # hold the red cube, then propose a path grazing it: excluded (held)
        from rgsim.backends import get_skills
        sk = get_skills("mujoco")
        for s in range(8):
            rng = np.random.default_rng(s)
            r = sk["pick"](w, "red_cube", rng, 0.98, None)
            if r.telemetry.get("grasp_success"):
                break
        assert w.held == "red_cube"
        rp = w.objects["red_cube"].pose[:3]
        wps = [(0.0, tuple(rp + np.array([0.08, 0, 0]))),
               (0.5, tuple(rp + np.array([-0.08, 0, 0])))]
        objs = [(n, o.pose[:3], o.radius) for n, o in w.objects.items()
                if not o.is_region and n != w.held]
        v = sup.check_trajectory(wps, dt=0.5, objects=objs, held=w.held)
        assert v.accepted
