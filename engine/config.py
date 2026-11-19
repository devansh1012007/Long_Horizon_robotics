"""Engine deployment configuration.

Every knob is env-driven so the same code runs as:
  - a loopback sidecar spawned by the Next.js gateway (default),
  - a containerized service (docker-compose sets ENGINE_HOST=0.0.0.0),
  - a horizontally scaled cluster (share ENGINE_DATA_DIR on a volume).

Layout convention: this package lives in <repo>/engine, the validated
research core in <repo>/recovergraph. Override RG_ROOT / ENGINE_DATA_DIR
to relocate either.
"""
from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """<repo> = parent of this engine package."""
    return Path(__file__).resolve().parents[1]


def _env_path(var: str, default: Path) -> Path:
    v = os.environ.get(var)
    return Path(v).expanduser().resolve() if v else default


RG_ROOT = _env_path("RG_ROOT", repo_root() / "recovergraph")
DATA_DIR = _env_path("ENGINE_DATA_DIR", repo_root() / "data" / "engine")
ARTIFACT = _env_path(
    "ENGINE_LEARNED_ARTIFACT", RG_ROOT / "configs" / "learned_artifact_v1.json")

# Ensure the research core is importable no matter where we run from.
import sys  # noqa: E402
if str(RG_ROOT) not in sys.path:
    sys.path.insert(0, str(RG_ROOT))

DATA_DIR.mkdir(parents=True, exist_ok=True)


def _env_int(var: str, default: int) -> int:
    try:
        return int(os.environ.get(var, default))
    except (TypeError, ValueError):
        return default


LIMITS = {
    "workers": _env_int("ENGINE_WORKERS", 3),            # concurrent job threads
    "max_queue": _env_int("ENGINE_MAX_QUEUE", 48),       # queued jobs before 503
    "max_systems": _env_int("ENGINE_MAX_SYSTEMS", 12),
    "max_horizons": _env_int("ENGINE_MAX_HORIZONS", 6),
    "max_episodes": _env_int("ENGINE_MAX_EPISODES", 100),  # per (system, horizon)
    "max_total": _env_int("ENGINE_MAX_TOTAL", 600),      # episodes per job
    "max_active_per_user": _env_int("ENGINE_MAX_ACTIVE_PER_USER", 2),
}

HOST = os.environ.get("ENGINE_HOST", "127.0.0.1")
PORT = _env_int("ENGINE_PORT", 3031)

HORIZONS = (1, 2, 3, 5, 8)
