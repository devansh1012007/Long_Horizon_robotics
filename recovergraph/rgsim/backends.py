"""Backend selection for rgsim: kinematic desktop sim vs MuJoCo physics.

ADR-001: the MuJoCo backend replaces the kinematic kernel while exposing the
identical world/skill interface to rg_core and the EpisodeRunner, so all
downstream code (systems, experiments, engine service, ROS 2 nodes) stays
backend-agnostic. Both backends sample the *same* scene for a given seed
(paired scenes), which is what makes cross-backend comparisons valid.

Public surface (used by rgsim.pipeline and tests):
  make_world(seed, cubes, backend="kinematic"|"mujoco", with_cyl=True)
  get_skills(backend) -> {"pick": fn, "move": fn, "place": fn}
"""
from __future__ import annotations

KINEMATIC = "kinematic"
MUJOCO = "mujoco"
BACKENDS = (KINEMATIC, MUJOCO)

from .world import World, generate_scene, rest_z  # noqa: F401  (re-export)


def make_world(seed: int, cubes: list, backend: str = KINEMATIC,
               with_cyl: bool = True):
    """Build a simulation world for `backend`.

    kinematic : pure-Python desktop kinematics (the M1 validated kernel)
    mujoco    : MuJoCo physics over the identically-sampled scene (from_scene
                rebuilds the kinematic layout as an MJCF model)
    """
    if backend == KINEMATIC:
        return generate_scene(seed, cubes, with_cyl)
    if backend == MUJOCO:
        from .mujoco_world import MuJoCoWorld   # lazy: mujoco import is heavy
        return MuJoCoWorld.from_scene(seed, cubes, with_cyl)
    raise ValueError(f"unknown backend {backend!r}; valid: {BACKENDS}")


def get_skills(backend: str = KINEMATIC) -> dict:
    """Skill implementations for `backend`, keyed 'pick' | 'move' | 'place'.

    Every skill has the same call signature across backends:
        fn(world, obj_or_target, rng, ...) -> SkillResult
    so the EpisodeRunner pipeline is shared verbatim.
    """
    if backend == KINEMATIC:
        from . import skills
        return {"pick": skills.execute_pick, "move": skills.execute_move,
                "place": skills.execute_place}
    if backend == MUJOCO:
        from . import mujoco_skills
        return {"pick": mujoco_skills.execute_pick,
                "move": mujoco_skills.execute_move,
                "place": mujoco_skills.execute_place}
    raise ValueError(f"unknown backend {backend!r}; valid: {BACKENDS}")


def require_backend(backend: str) -> str:
    """Validate a backend name (used by the engine service API)."""
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; valid: {BACKENDS}")
    if backend == MUJOCO:
        try:
            import mujoco  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "backend 'mujoco' requested but the mujoco package is not "
                "installed in this environment") from exc
    return backend
