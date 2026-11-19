"""RecoverGraph engine service core.

Wraps the validated research engine (rg_core / rgsim / systems /
experiments) with:
  - a bounded worker pool + job registry (queue-safe for many users),
  - a telemetry EpisodeDB subclass that streams live events to socket.io,
  - per-system aggregates + paired statistics (Wilson CI, paired bootstrap,
    McNemar, Wilcoxon) computed with the pre-registered experiments.stats,
  - cumulative counters exposed at /metrics (Prometheus text format).

The research engine itself is imported untouched; the interface guarantees
protected by the recovergraph test suite remain intact. Paths and limits
come from engine.config (env-driven).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from config import ARTIFACT, DATA_DIR, HORIZONS, LIMITS, RG_ROOT

from rg_core.episode_db import EpisodeDB          # noqa: E402
from rg_core.taskgen import make_task             # noqa: E402
from rgsim.pipeline import EpisodeRunner          # noqa: E402
from systems import make_system                   # noqa: E402
from experiments.stats import (                   # noqa: E402
    paired_bootstrap_ci, mcnemar_exact, wilcoxon_signed_rank, wilson_ci)

CONDITIONS = ("none", "single", "compound")
FAULTS = ["f_none", "f_miss", "f_slip", "f_pose", "f_collision",
          "f_perception", "f_controller", "stale_dependency"]
FAULT_LABELS = {
    "f_none": "No fault (control)",
    "f_miss": "Grasp miss (offset)",
    "f_slip": "In-grip slip",
    "f_pose": "Object pose shift",
    "f_collision": "Collision / blocked",
    "f_perception": "Perception dropout / noise",
    "f_controller": "Controller tracking fault",
    "stale_dependency": "Stale dependency (moved object)",
}
SYSTEM_META = [
    {"id": "RG",  "name": "RecoverGraph", "family": "causal",
     "desc": "Full stack: Mahalanobis divergence, Bayes diagnosis, "
             "cost-optimized recovery, DAG invalidation, loop guard."},
    {"id": "B1",  "name": "B1 - No recovery", "family": "baseline",
     "desc": "Low-level skills only; divergence ignored."},
    {"id": "B2",  "name": "B2 - Fixed retry", "family": "baseline",
     "desc": "Blind retry up to 3 attempts."},
    {"id": "B3",  "name": "B3 - Full replan", "family": "baseline",
     "desc": "Replan from current observed state on every divergence."},
    {"id": "B4",  "name": "B4 - Verification orchestrator", "family": "baseline",
     "desc": "LADDER-style hard effect verification + re-observe/retry."},
    {"id": "B5",  "name": "B5 - Fault pipeline", "family": "baseline",
     "desc": "Classic threshold fault pipeline; deterministic map to "
             "forward/backward actions. Strongest baseline."},
    {"id": "A1",  "name": "A1 - No diagnosis", "family": "ablation",
     "desc": "RG without the Bayes diagnoser."},
    {"id": "A2",  "name": "A2 - No invalidation", "family": "ablation",
     "desc": "RG without dependency-graph invalidation."},
    {"id": "A3",  "name": "A3 - Point estimates", "family": "ablation",
     "desc": "Detection without uncertainty (no Mahalanobis)."},
    {"id": "A4",  "name": "A4 - Fixed priority", "family": "ablation",
     "desc": "RG with fixed-priority recovery instead of cost optimization."},
    {"id": "A5",  "name": "A5 - Learned likelihood", "family": "ablation",
     "desc": "RG with learned likelihood artifact (falls back honestly)."},
    {"id": "A6",  "name": "A6 - Double noise (RG)", "family": "ablation",
     "desc": "RG under the pre-registered doubled perception-noise model."},
]
SYSTEM_IDS = [s["id"] for s in SYSTEM_META]

# --------------------------------------------------------------------------- #
# JSON safety
# --------------------------------------------------------------------------- #
def json_safe(obj, _depth: int = 0):
    if _depth > 6:
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): json_safe(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v, _depth + 1) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        if isinstance(obj, str) and len(obj) > 400:
            return obj[:400] + "..."
        return obj
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", "replace")[:200]
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def _round(v, nd=4):
    return round(float(v), nd) if v is not None else None


# --------------------------------------------------------------------------- #
# Emitter bridge (worker threads -> asyncio socket.io)
# --------------------------------------------------------------------------- #
class Emitter:
    """Thread-safe bridge: worker threads push socket.io emits onto the
    asyncio loop owned by the web layer."""

    def __init__(self):
        self.sio = None
        self.loop = None

    def bind(self, sio, loop):
        self.sio, self.loop = sio, loop

    def emit(self, event: str, data: dict, room: str | None = None):
        if not (self.sio and self.loop):
            return
        try:
            import asyncio
            coro = self.sio.emit(event, json_safe(data), room=room)
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        except RuntimeError:
            pass


_emitter = Emitter()


def set_emitter(em: Emitter):
    global _emitter
    _emitter = em


def get_emitter() -> Emitter:
    return _emitter


# --------------------------------------------------------------------------- #
# Telemetry DB: persists everything (same schema) + streams key events
# --------------------------------------------------------------------------- #
class TelemetryDB(EpisodeDB):
    """Persists every record exactly like EpisodeDB and additionally streams
    key events to socket.io (capped per episode to protect the channel)."""

    def __init__(self, path: str, job_id: str, room: str, cap: int = 36):
        super().__init__(path)
        self.job_id = job_id
        self.room = room
        self.cap = cap
        self._n = 0

    def _emit(self, etype: str, data: dict, force: bool = False):
        self._n += 1
        if force or self._n <= self.cap:
            payload = {"job": self.job_id, "etype": etype, **data}
            job = REGISTRY.jobs.get(self.job_id)
            if job is not None:
                job.live_buffer.append(json_safe(payload))
            _emitter.emit("ep:event", payload, room=self.room)

    def reset_budget(self, ep_id: str):
        self._n = 0

    def log_event(self, ep_id: str, t: float, node: str, etype: str,
                  payload: dict) -> None:
        super().log_event(ep_id, t, node, etype, payload)
        if etype == "outcome":
            # finish_episode() already streams a structured outcome event
            return
        self._emit(etype, {"ep": ep_id, "t": _round(t, 3), "node": node,
                           "payload": payload})

    def log_ledger(self, entry) -> None:
        super().log_ledger(entry)
        if entry.divergence_flagged:
            self._emit("divergence", {
                "ep": entry.episode_id, "step": entry.step,
                "subgoal": entry.subgoal, "skill": entry.skill,
                "D": _round(entry.divergence_D, 2),
                "fault_free": bool(entry.fault_free)})

    def log_diagnosis(self, ep_id: str, step: int, diagnosis,
                      truth: str) -> None:
        super().log_diagnosis(ep_id, step, diagnosis, truth)
        top = sorted(diagnosis.posterior.items(), key=lambda kv: -kv[1])[:3]
        self._emit("diagnosis", {
            "ep": ep_id, "step": step, "argmax": diagnosis.argmax,
            "truth": truth, "entropy": _round(diagnosis.entropy, 3),
            "top": [[k, _round(v)] for k, v in top]}, force=True)

    def log_recovery(self, ep_id: str, step: int, decision,
                     result: str) -> None:
        super().log_recovery(ep_id, step, decision, result)
        self._emit("recovery", {
            "ep": ep_id, "step": step, "chosen": decision.chosen,
            "result": result,
            "cost": {k: _round(v, 2) for k, v in
                     (decision.cost_terms or {}).items()}}, force=True)

    def finish_episode(self, ep_id: str, outcome: str, success: bool,
                       wall_time: float, metrics: dict) -> None:
        super().finish_episode(ep_id, outcome, success, wall_time, metrics)
        self._emit("outcome", {
            "ep": ep_id, "outcome": outcome, "success": bool(success),
            "extra_actions": metrics.get("extra_actions"),
            "recovery_time_s": _round(metrics.get("recovery_time_s"), 3),
            "escalations": metrics.get("escalations"),
            "interventions": metrics.get("interventions"),
            "steps": metrics.get("steps")}, force=True)


# --------------------------------------------------------------------------- #
# Job registry
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    id: str
    user_id: str
    label: str
    spec: dict
    status: str = "queued"          # queued | running | done | failed
    progress: int = 0
    total: int = 0
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    error: str | None = None
    summary: dict | None = None
    stats: list | None = None
    rows: list | None = None
    live_buffer: deque = field(      # ring buffer for late-joiner replay
        default_factory=lambda: deque(maxlen=250))

    def public(self, with_rows: bool = False) -> dict:
        d = {"id": self.id, "user_id": self.user_id, "label": self.label,
             "spec": self.spec, "status": self.status,
             "progress": self.progress, "total": self.total,
             "created": self.created, "started": self.started,
             "finished": self.finished, "error": self.error,
             "summary": self.summary, "stats": self.stats}
        if with_rows:
            d["rows"] = self.rows
        return d

    def snapshot(self) -> dict:
        return self.public(with_rows=True)


class Registry:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=LIMITS["workers"])
        self.active = 0                 # threads currently running jobs
        # Cumulative lifetime counters, rendered at /metrics.
        self.totals = {"submitted": 0, "done": 0, "failed": 0,
                       "episodes": 0}

    def submit(self, job: Job):
        with self.lock:
            self.jobs[job.id] = job
            pending = sum(1 for j in self.jobs.values()
                          if j.status in ("queued", "running"))
            self.totals["submitted"] += 1
        if pending > LIMITS["max_queue"]:
            job.status = "failed"
            job.error = "Service busy: queue limit reached, try later."
            self.totals["failed"] += 1
            self.persist(job)
            raise RuntimeError(job.error)
        self.executor.submit(_run_job_safe, job)
        self.persist(job)

    def load(self):
        for f in sorted(DATA_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text())
                job = Job(id=d["id"], user_id=d.get("user_id", "legacy"),
                          label=d.get("label", ""), spec=d["spec"],
                          status=d["status"], progress=d.get("progress", 0),
                          total=d.get("total", 0), created=d.get("created", 0),
                          started=d.get("started"), finished=d.get("finished"),
                          error=d.get("error"), summary=d.get("summary"),
                          stats=d.get("stats"), rows=d.get("rows"))
                if job.status in ("queued", "running"):
                    job.status = "failed"
                    job.error = "Interrupted by service restart."
                self.jobs[job.id] = job
            except Exception:
                continue
        self.prune()

    def persist(self, job: Job):
        try:
            (DATA_DIR / f"{job.id}.json").write_text(
                json.dumps(job.snapshot(), default=str))
        except Exception:
            pass

    def prune(self, keep: int = 60):
        files = sorted(DATA_DIR.glob("*.json"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[keep:]:
            self.jobs.pop(f.stem, None)
            for suffix in (".json", ".sqlite"):
                p = DATA_DIR / f"{f.stem}{suffix}"
                if p.exists():
                    p.unlink(missing_ok=True)


REGISTRY = Registry()


def episode_detail(job_id: str, ep_id: str) -> dict | None:
    path = DATA_DIR / f"{job_id}.sqlite"
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        ep = conn.execute("SELECT * FROM episodes WHERE id=?",
                          (ep_id,)).fetchone()
        if not ep:
            return None
        events = conn.execute(
            "SELECT t,node,type,payload_json FROM events WHERE episode_id=?"
            " ORDER BY rowid LIMIT 500", (ep_id,)).fetchall()
        diagnoses = conn.execute(
            "SELECT step,posterior_json,argmax,truth FROM diagnoses WHERE"
            " episode_id=? ORDER BY rowid", (ep_id,)).fetchall()
        recoveries = conn.execute(
            "SELECT step,candidates_json,chosen,cost_terms_json,result FROM"
            " recoveries WHERE episode_id=? ORDER BY rowid", (ep_id,)).fetchall()
        return {
            "episode": dict(ep),
            "metrics": json.loads(ep["metrics_json"]) if ep["metrics_json"] else {},
            "events": [{"t": _round(r["t"], 3), "node": r["node"],
                        "etype": r["type"],
                        "payload": json.loads(r["payload_json"] or "{}")}
                       for r in events],
            "diagnoses": [{"step": r["step"], "argmax": r["argmax"],
                           "truth": r["truth"],
                           **json.loads(r["posterior_json"] or "{}")}
                          for r in diagnoses],
            "recoveries": [{"step": r["step"], "chosen": r["chosen"],
                            "result": r["result"],
                            "cost_terms": json.loads(r["cost_terms_json"] or "{}")}
                           for r in recoveries],
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def _run_job_safe(job: Job):
    em = get_emitter()
    with REGISTRY.lock:
        REGISTRY.active += 1
    job.status = "running"
    job.started = time.time()
    job.total = (len(job.spec["systems"]) * len(job.spec["horizons"]) *
                 job.spec["episodes"])
    em.emit("job:update", job.public(), room="global")
    try:
        _execute_job(job, em)
        job.status = "done"
        with REGISTRY.lock:
            REGISTRY.totals["done"] += 1
            REGISTRY.totals["episodes"] += job.total
    except Exception as exc:                       # noqa: BLE001
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1200:]}"
        with REGISTRY.lock:
            REGISTRY.totals["failed"] += 1
    finally:
        with REGISTRY.lock:
            REGISTRY.active -= 1
        job.finished = time.time()
        REGISTRY.persist(job)
        em.emit("job:update", job.public(), room="global")


def _execute_job(job: Job, em: Emitter):
    spec = job.spec
    systems, horizons = spec["systems"], spec["horizons"]
    condition, fault = spec["condition"], spec.get("fault")
    episodes, base_seed = spec["episodes"], spec["base_seed"]
    backend = spec.get("backend", "kinematic")
    seeds = [base_seed + i for i in range(episodes)]
    last_emit = 0.0

    db = TelemetryDB(str(DATA_DIR / f"{job.id}.sqlite"), job.id, room=job.id)
    rows: list[dict] = []
    for sys_id in systems:
        sys_run = "RG" if sys_id == "A6" else sys_id
        noise = None
        if sys_id == "A6":
            from experiments.run_episodes import A6_NOISE
            noise = A6_NOISE
        artifact = ARTIFACT if sys_run in ("A5", "RG-LEARNED") else None
        for h in horizons:
            for seed in seeds:
                ep_id = f"ep_{sys_id}_{condition}_h{h}_{seed}"
                if fault:
                    ep_id += f"_{fault}"
                task = make_task(h, seed % 20, seed)
                system = make_system(sys_run, artifact)
                db.reset_budget(ep_id)
                runner = EpisodeRunner(
                    system, task, seed=seed, condition=condition, db=db,
                    ep_id=ep_id, backend=backend,
                    faults=[fault] if fault else None, noise_cfg=noise)
                res = runner.run()
                m = res["metrics"]
                labels = [e["label"] for e in res["faults"]]
                dc = m["diag_correct"]
                rows.append({
                    "system": sys_id, "condition": condition, "horizon": h,
                    "seed": seed, "template": seed % 20, "backend": backend,
                    "ep_id": ep_id,
                    "outcome": res["outcome"],
                    "success": int(bool(m["success"])),
                    "extra_actions": m["extra_actions"],
                    "recovery_time_s": _round(m["recovery_time_s"], 3),
                    "vetoes": m["vetoes"],
                    "unsafe_executions": m["unsafe_executions"],
                    "interventions": m["interventions"],
                    "escalations": m["escalations"],
                    "flags": m["flags"],
                    "flags_fault_free": m["flags_fault_free"],
                    "steps": m["steps"],
                    "diag_correct": (None if dc is None else int(dc)),
                    "regret": _round(m["regret"], 3),
                    "fault": fault or ("|".join(labels) if labels else "none"),
                    "task_id": res["task_id"],
                })
                job.progress += 1
                now = time.time()
                if now - last_emit > 0.25 or job.progress == job.total:
                    last_emit = now
                    em.emit("job:update", job.public(), room="global")
    db.close()

    job.rows = rows
    job.summary = compute_summary(rows, systems)
    job.stats = compute_paired_stats(rows, systems)


def compute_summary(rows: list[dict], systems: list[str]) -> dict:
    out = {}
    for sys_id in systems:
        rs = [r for r in rows if r["system"] == sys_id]
        if not rs:
            out[sys_id] = {"n": 0}
            continue
        n = len(rs)
        succ = sum(r["success"] for r in rs)
        p, lo, hi = wilson_ci(succ, n)
        rec = [r["recovery_time_s"] for r in rs if r["recovery_time_s"] > 0]
        dc = [(r["diag_correct"]) for r in rs if r["diag_correct"] is not None]
        fa = sum(1 for r in rs if r["flags_fault_free"] > 0)
        out[sys_id] = {
            "n": n, "successes": succ,
            "success_rate": _round(p), "ci_lo": _round(lo), "ci_hi": _round(hi),
            "extra_actions_mean": _round(
                sum(r["extra_actions"] for r in rs) / n, 2),
            "recovery_time_mean": _round(sum(rec) / len(rec), 3) if rec else 0.0,
            "recovery_n": len(rec),
            "diag_n": len(dc),
            "diag_accuracy": _round(sum(dc) / len(dc), 3) if dc else None,
            "escalations": sum(r["escalations"] for r in rs),
            "interventions": sum(r["interventions"] for r in rs),
            "unsafe_executions": sum(r["unsafe_executions"] for r in rs),
            "false_alarm_episode_rate": _round(fa / n, 3),
        }
    return out


def compute_paired_stats(rows: list[dict], systems: list[str]) -> list:
    """RG vs every other system, paired by (horizon, seed). Uses the
    pre-registered statistics: paired bootstrap CI, McNemar exact, Wilcoxon."""
    if "RG" not in systems:
        return []
    rg = {(r["horizon"], r["seed"]): r for r in rows if r["system"] == "RG"}
    stats_out = []
    for sys_id in systems:
        if sys_id == "RG":
            continue
        other = {(r["horizon"], r["seed"]): r for r in rows
                 if r["system"] == sys_id}
        keys = sorted(set(rg) & set(other))
        if not keys:
            continue
        x = [rg[k]["success"] for k in keys]           # RG successes
        y = [other[k]["success"] for k in keys]        # baseline successes
        diffs = [a - b for a, b in zip(x, y)]
        mean, lo, hi = paired_bootstrap_ci(diffs)
        _, _, p_mcn = mcnemar_exact(x, y)
        ea_x = [rg[k]["extra_actions"] for k in keys]
        ea_y = [other[k]["extra_actions"] for k in keys]
        try:
            _, p_wil = wilcoxon_signed_rank(ea_x, ea_y)
        except ValueError:
            p_wil = 1.0
        stats_out.append({
            "vs": sys_id, "n_pairs": len(keys),
            "delta_success_mean": _round(mean), "delta_ci_lo": _round(lo),
            "delta_ci_hi": _round(hi), "ci_excludes_zero": bool(lo > 0 or hi < 0),
            "mcnemar_p": _round(p_mcn), "wilcoxon_p": _round(p_wil),
        })
    return stats_out
