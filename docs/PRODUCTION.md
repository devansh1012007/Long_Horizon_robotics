# Production guide

How the RecoverGraph console runs in production, how to scale it for many
concurrent users, and what to watch.

## Topology

```
            ┌──────────────────────────── docker network (internal) ─────────────┐
internet ──►│ nginx :80 ──► dashboard :3000 (Next.js standalone, stateless)      │
            │    │                  │                                            │
            │    └─ /socket.io ──► │                                            │
            │                      ▼                                            │
            │              engine :3031 (FastAPI, never exposed)                │
            │                worker pool (ENGINE_WORKERS) → simulation jobs     │
            │                job registry + episode DBs on the rg-data volume   │
            └───────────────────────────────────────────────────────────────────┘
```

- **dashboard** — Next.js 16 standalone server. Stateless: auth sessions in
  Prisma, job metadata write-through, rate limiting per process. Scale
  horizontally behind nginx (`upstream rg_dashboard { server a:3000; server b:3000; }`).
- **engine** — owns the research core. Bounded worker pool
  (`ENGINE_WORKERS`, default 3) with a queue cap (`ENGINE_MAX_QUEUE`, 48 →
  HTTP 503 back-pressure). Each job writes a per-job SQLite episode DB.
- **nginx** — the only public port. Same-origin `/socket.io/` upgrade to the
  engine; everything else to the dashboard. The engine HTTP API is reachable
  only server-side (`ENGINE_URL=http://engine:3031`).

## Two engine modes

| mode | when | config |
|---|---|---|
| sidecar (dev / single box) | dashboard spawns `../engine` automatically | unset `ENGINE_URL` (default loopback) |
| remote (docker / cluster) | engine runs as its own container/cluster | dashboard: `ENGINE_URL=http://engine:3031` — never spawns |

## Scaling for many users

1. **Episodes are cheap (~5–10 ms each kinematic).** A 3-worker engine
   digests thousands of episodes per minute. Contention shows up as queue
   depth, not latency: watch `rg_engine_queued_jobs` (Prometheus) or the
   ops panel. If the queue sits above ~20, raise `ENGINE_WORKERS` (I/O-free
   pure-Python workers scale with cores) or run more engine replicas.
2. **Multiple engine replicas**: the dashboard holds no engine affinity, so
   HTTP calls load-balance freely. socket.io rooms need sticky sessions —
   `hash $arg_EIO consistent` on the engine upstream in nginx, or use the
   Redis adapter if you move off single-node rooms.
3. **Fairness**: `MAX_ACTIVE_JOBS_PER_USER` (default 2) caps concurrent
   queued/running jobs per operator; `RULES` in `src/lib/rate-limit.ts`
   token-buckets auth/submit/read/heavy per user/IP.
4. **Database**: SQLite (WAL-friendly single-writer) is fine for one node;
   for a multi-writer cluster swap `DATABASE_URL` to Postgres — the Prisma
   schema is portable. Per-job episode DBs are already file-per-job.
5. **Data layout**: everything durable lives under `data/`
   (`dashboard.db`, `engine/job_*.json|.sqlite`) — mount it as a volume
   (compose does). Nothing under `.next/` is durable: never point
   `ENGINE_DATA_DIR` inside `.next/`, rebuilds wipe it.

## Security checklist

- [ ] HTTPS terminated at nginx (or your LB) → set `COOKIE_SECURE=1` for the
      dashboard (session cookies become `Secure`).
- [ ] Engine stays internal: never publish :3031 outside the compose
      network; the dashboard is the authenticated gateway.
- [ ] Registration is open by design for team tools; for internet-facing
      deploys add an invite gate in `src/app/api/auth/register/route.ts`.
- [ ] Rate limits: tune `RULES` if you front with a shared proxy (the
      in-memory limiter is per process; per-instance limits multiply by the
      number of dashboard replicas — move to Redis when that matters).
- [ ] `CLIENT_MAX_BODY_SIZE` is 2m in `deploy/nginx.conf`; jobs validate
      server-side anyway (systems/horizons/episodes caps mirror the engine).

## Observability

| surface | endpoint | notes |
|---|---|---|
| engine liveness | `GET /health` | version, uptime, pool, jobs by status, totals |
| Prometheus | `GET /metrics` | `rg_engine_*` counters/gauges (jobs, episodes, queue depth) |
| platform stats | `GET /api/ops` (auth) | users, jobs, persisted episodes, engine reachability |
| job telemetry | socket.io rooms | `job:update`, `ep:event` (divergence / diagnosis / recovery / outcome) |

Scrape `/metrics` from Prometheus; alert on
`rg_engine_jobs_total{state="failed"}` growth and
`rg_engine_queued_jobs > 20`.

## Runbook

```bash
make smoke            # engine-only e2e (boots engine on :3131, real job)
bash scripts/e2e.sh   # full-stack e2e (fresh engine :3032 + standalone dashboard)
docker compose logs -f engine dashboard
make down             # stop the stack (data volume persists)
```

Job artifacts live in `data/engine/job_<id>.sqlite` — drill-down via
`GET /api/jobs/<id>/episodes/<ep>`. The engine prunes its registry to the
newest 60 jobs; delete explicitly with `DELETE /api/jobs/<id>` (Prisma copy
is kept for the leaderboard until the owner deletes the job).
