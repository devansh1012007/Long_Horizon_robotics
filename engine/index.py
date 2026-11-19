"""RecoverGraph Engine Service — production worker service.

HTTP (default 127.0.0.1:3031; set ENGINE_HOST/ENGINE_PORT to relocate):
  GET    /health                     service + worker-pool status
  GET    /metrics                    Prometheus text exposition
  GET    /capabilities               systems / faults / conditions / limits
  POST   /jobs                       submit batch job -> {job_id}
  GET    /jobs?user_id=              list jobs (compact)
  GET    /jobs/{id}                  status (+summary/stats when done)
  GET    /jobs/{id}/result           full result incl. per-episode rows
  GET    /jobs/{id}/episodes/{ep}    episode drill-down from the episode DB
  DELETE /jobs/{id}                  drop a finished job from the registry

Socket.io (same port, path "/socket.io/", v4 protocol):
  join room "<job_id>" or "global"; receives job:update and ep:event.

The sandbox gateway forwards websocket traffic at path "/" (with
?XTransformPort=3031), so the ASGI dispatcher below rewrites "/" to the
socket.io path for compatibility; standard clients use /socket.io/ as usual.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import socketio
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config import DATA_DIR, HORIZONS, LIMITS  # engine deployment config
from core import (CONDITIONS, Emitter, FAULTS, FAULT_LABELS, REGISTRY,
                  SYSTEM_IDS, SYSTEM_META, Job, episode_detail, set_emitter)

APP_VERSION = "rg-engine 1.2.0 (production, formal+MuJoCo validated core)"
STARTED = time.time()

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI(title="RecoverGraph Engine", version=APP_VERSION)
asgi = socketio.ASGIApp(sio, other_asgi_app=app)
EMITTER = Emitter()
set_emitter(EMITTER)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"])


@app.on_event("startup")
async def _startup() -> None:
    EMITTER.bind(sio, asyncio.get_running_loop())
    REGISTRY.load()


# --------------------------------------------------------------------------- #
# Socket.io
# --------------------------------------------------------------------------- #
@sio.event
async def connect(sid: str, environ: dict) -> None:
    await sio.enter_room(sid, "global")


@sio.event
async def join(sid: str, room: str) -> None:
    if isinstance(room, str) and (room == "global" or
                                  room in REGISTRY.jobs or
                                  room.startswith("job_")):
        await sio.enter_room(sid, room)
        # late-joiner replay: job snapshot + buffered telemetry tail
        job = REGISTRY.jobs.get(room)
        if job is not None:
            await sio.emit("job:update", job.public(), to=sid)
            for ev in list(job.live_buffer)[-120:]:
                await sio.emit("ep:event", ev, to=sid)


@sio.event
async def leave(sid: str, room: str) -> None:
    if isinstance(room, str):
        await sio.leave_room(sid, room)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class JobSpec(BaseModel):
    systems: list[str] = Field(..., min_length=1, max_length=LIMITS["max_systems"])
    horizons: list[int] = Field(..., min_length=1, max_length=LIMITS["max_horizons"])
    condition: str = "single"
    fault: str | None = None
    episodes: int = Field(15, ge=1, le=LIMITS["max_episodes"])
    base_seed: int = Field(4200, ge=0, le=2**31 - 1)
    backend: str = "kinematic"
    label: str = Field("", max_length=120)


class SubmitBody(BaseModel):
    user_id: str = "anonymous"
    spec: JobSpec


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health() -> dict:
    by_status = {"queued": 0, "running": 0, "done": 0, "failed": 0}
    for j in REGISTRY.jobs.values():
        by_status[j.status] = by_status.get(j.status, 0) + 1
    episodes = sum(len(j.rows or []) for j in REGISTRY.jobs.values())
    return {
        "status": "ok", "version": APP_VERSION,
        "uptime_s": round(time.time() - STARTED, 1),
        "pool": {"workers": LIMITS["workers"], "active": REGISTRY.active,
                 "queued": REGISTRY.executor._work_queue.qsize()},
        "jobs": by_status, "episodes_recorded": episodes,
        "totals": dict(REGISTRY.totals),
        "limits": LIMITS,
    }


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus text exposition (no third-party dependency needed)."""
    by_status = {"queued": 0, "running": 0, "done": 0, "failed": 0}
    for j in REGISTRY.jobs.values():
        by_status[j.status] = by_status.get(j.status, 0) + 1
    lines = [
        "# HELP rg_engine_up Engine process up (1 = running)",
        "# TYPE rg_engine_up gauge",
        "rg_engine_up 1",
        "# HELP rg_engine_uptime_seconds Engine process uptime",
        "# TYPE rg_engine_uptime_seconds gauge",
        f"rg_engine_uptime_seconds {round(time.time() - STARTED, 1)}",
        "# HELP rg_engine_jobs_total Lifetime job counters by outcome",
        "# TYPE rg_engine_jobs_total counter",
        f'rg_engine_jobs_total{{state="submitted"}} {REGISTRY.totals["submitted"]}',
        f'rg_engine_jobs_total{{state="done"}} {REGISTRY.totals["done"]}',
        f'rg_engine_jobs_total{{state="failed"}} {REGISTRY.totals["failed"]}',
        "# HELP rg_engine_episodes_total Lifetime episodes simulated",
        "# TYPE rg_engine_episodes_total counter",
        f'rg_engine_episodes_total {REGISTRY.totals["episodes"]}',
        "# HELP rg_engine_active_jobs Currently running jobs",
        "# TYPE rg_engine_active_jobs gauge",
        f"rg_engine_active_jobs {REGISTRY.active}",
        "# HELP rg_engine_queued_jobs Jobs waiting in the executor queue",
        "# TYPE rg_engine_queued_jobs gauge",
        f"rg_engine_queued_jobs {REGISTRY.executor._work_queue.qsize()}",
        "# HELP rg_engine_jobs_by_status Jobs in the registry by status",
        "# TYPE rg_engine_jobs_by_status gauge",
    ]
    for st, n in by_status.items():
        lines.append(f'rg_engine_jobs_by_status{{status="{st}"}} {n}')
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/capabilities")
async def capabilities() -> dict:
    return {"systems": SYSTEM_META, "faults": FAULTS,
            "fault_labels": FAULT_LABELS, "conditions": list(CONDITIONS),
            "horizons": list(HORIZONS), "limits": LIMITS}


def _validate_spec(spec: JobSpec) -> None:
    if any(s not in SYSTEM_IDS for s in spec.systems):
        raise HTTPException(400, f"unknown system; valid: {SYSTEM_IDS}")
    if spec.condition not in CONDITIONS:
        raise HTTPException(400, f"condition must be one of {CONDITIONS}")
    if spec.fault is not None and spec.fault not in FAULTS:
        raise HTTPException(400, f"unknown fault; valid: {FAULTS + [None]}")
    if any(h not in HORIZONS for h in spec.horizons):
        raise HTTPException(400, f"horizons must be chosen from {list(HORIZONS)}")
    if spec.backend not in ("kinematic", "mujoco"):
        raise HTTPException(400, "backend must be 'kinematic' or 'mujoco'")
    if spec.backend == "mujoco":
        try:
            import mujoco  # noqa: F401
        except ImportError:
            raise HTTPException(
                503, "backend 'mujoco' is not installed in this engine "
                     "environment (pip install mujoco)")
    total = len(spec.systems) * len(spec.horizons) * spec.episodes
    if total > LIMITS["max_total"]:
        raise HTTPException(
            400, f"job too large: {total} episodes (max {LIMITS['max_total']}); "
                 "reduce systems, horizons or episodes.")


@app.post("/jobs")
async def submit(body: SubmitBody) -> dict:
    spec = body.spec
    _validate_spec(spec)
    job = Job(id=f"job_{uuid.uuid4().hex[:12]}", user_id=body.user_id,
              label=spec.label.strip() or
              f"{len(spec.systems)} systems x h{min(spec.horizons)}-"
              f"{max(spec.horizons)} x {spec.episodes} eps",
              spec=spec.model_dump())
    try:
        REGISTRY.submit(job)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    return {"job_id": job.id, "status": job.status, "total": job.total}


@app.get("/jobs")
async def list_jobs(user_id: str | None = None, limit: int = 40) -> dict:
    jobs = sorted(REGISTRY.jobs.values(), key=lambda j: j.created,
                  reverse=True)
    if user_id:
        jobs = [j for j in jobs if j.user_id == user_id]
    return {"jobs": [j.public() for j in jobs[:limit]]}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = REGISTRY.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found (may have been pruned)")
    return job.public()


@app.get("/jobs/{job_id}/result")
async def job_result(job_id: str) -> dict:
    job = REGISTRY.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found (may have been pruned)")
    if job.status != "done":
        raise HTTPException(409, f"job not finished (status={job.status})")
    return job.public(with_rows=True)


@app.get("/jobs/{job_id}/episodes/{ep_id}")
async def job_episode(job_id: str, ep_id: str) -> dict:
    detail = episode_detail(job_id, ep_id)
    if detail is None:
        raise HTTPException(404, "episode not found in this job")
    return detail


@app.delete("/jobs/{job_id}")
async def delete_job(job_id: str) -> dict:
    job = REGISTRY.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status in ("queued", "running"):
        raise HTTPException(409, "cannot delete a running job")
    REGISTRY.jobs.pop(job_id, None)
    for suffix in (".json", ".sqlite"):
        (DATA_DIR / f"{job_id}{suffix}").unlink(missing_ok=True)
    return {"deleted": job_id}


async def gateway(scope, receive, send):
    """ASGI dispatcher: the sandbox gateway forwards WebSocket/polling
    traffic at path "/" (with ?XTransformPort=3031), while FastAPI serves
    HTTP routes elsewhere. Rewrite "/" to the engine.io path so socket.io
    clients connect through the gateway; everything else hits FastAPI."""
    if scope["type"] in ("http", "websocket") and scope.get("path") in ("", "/"):
        scope = dict(scope)
        scope["path"] = "/socket.io/"
    await asgi(scope, receive, send)


if __name__ == "__main__":
    import os
    uvicorn.run(gateway,
                host=os.environ.get("ENGINE_HOST", "127.0.0.1"),
                port=int(os.environ.get("ENGINE_PORT", "3031")),
                log_level="warning")
