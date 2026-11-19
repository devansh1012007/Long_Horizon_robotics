# Long_Horizon_robotics — RecoverGraph

Causal, uncertainty-aware recovery for long-horizon robot manipulation,
shipped as a **multi-user production web application** on top of a
pre-registered, formally evaluated research core.

When a manipulation action diverges from its expected effect, RecoverGraph
answers the three questions that decide whether a robot recovers or flails:

1. **Detection** — did the effect diverge? (Mahalanobis divergence with
   chi-squared thresholds; step-wise Bonferroni; 0.74% false-alarm rate on
   fault-free steps)
2. **Diagnosis** — which fault produced it, with calibrated uncertainty?
   (Bayesian diagnosis over 7 mechanisms; macro-F1 0.89–0.91)
3. **Recovery** — what is the lowest-cost safe repair? (affordance-filtered
   cost optimization, task-graph invalidation, loop guard, independent
   safety supervisor — **zero unsafe executions by construction**)

## Headline results (pre-registered formal test split, both backends pooled)

| system | n | success | Wilson 95% CI |
|---|---|---|---|
| **RG (RecoverGraph)** | 2016 | **0.788** | [0.769, 0.805] |
| B5 — fault pipeline (strongest baseline) | 2016 | 0.615 | [0.593, 0.636] |
| B4 — verification orchestrator | 2016 | 0.380 | [0.358, 0.401] |
| B3 — full replan | 2016 | 0.473 | [0.452, 0.495] |
| B2 — fixed retry | 2016 | 0.374 | [0.354, 0.396] |
| B1 — no recovery | 2016 | 0.096 | [0.084, 0.109] |

Primary paired comparisons (RG − baseline, McNemar p = 0 for all):
**+0.69 vs B1 · +0.41 vs B2 · +0.32 vs B3 · +0.41 vs B4 · +0.17 vs B5**
(all 95% CIs exclude zero). Full tables, ablations (A1–A6), MuJoCo-backend
results, LADDER-aligned study and the go/no-go verdicts live in
[`recovergraph/experiments/results/`](recovergraph/experiments/results/)
with the analysis in [`recovergraph/docs/`](recovergraph/docs/) (spec,
pre-registration, ADRs, literature audit, paper draft).

## What is in this repo

```
├── src/ + prisma/ + package.json   Next.js 16 console (the product):
│                                   auth, quotas, rate limits, job control,
│                                   episode browser, leaderboard, ops panel,
│                                   live socket.io telemetry
├── engine/                         FastAPI + socket.io engine service:
│                                   bounded worker pool, persistent job
│                                   registry, Prometheus /metrics, MuJoCo
│                                   option, pytest suite, Dockerfile
├── recovergraph/                   the research program (frozen interfaces):
│                                   rg_core algorithms, rgsim (kinematic +
│                                   MuJoCo backends), systems B1–B5/RG/A1–A6,
│                                   experiments + formal results, 231 tests,
│                                   ROS 2 Jazzy packages, docs
├── deploy/                         dashboard Dockerfile + nginx.conf
├── docker-compose.yml              one-command production stack
├── scripts/                        smoke.sh (engine e2e), e2e.sh (full-stack e2e)
└── .github/workflows/ci.yml        pytest + dashboard build on every push
```

```
 browser ──► nginx :80 ──► dashboard (Next.js :3000)
                │               │  auth · quotas · rate limits · Prisma
                │               ├─► engine (FastAPI :3031, internal)
                │               │     worker pool → rg_core/rgsim sims
                └─ /socket.io ──┘   live job + episode telemetry
```

## Quickstart (development)

Prerequisites: Python 3.11+, [Bun](https://bun.sh) (or Node 20+), curl.

```bash
# 1. web deps + database schema
make install                 # bun install + prisma generate
make db                      # creates data/dashboard.db (SQLite)

# 2. python deps for the engine + research core
pip install -r engine/requirements.txt mujoco   # mujoco optional

# 3. run everything
make dev                     # dashboard :3000, auto-spawns engine sidecar :3031
#   or run the engine standalone: make engine
```

Open http://localhost:3000, register an operator account, pick systems
(B1–B5, RG, ablations), a fault condition, episode counts — and watch
divergence / diagnosis / recovery events stream live while the paired
statistics (Wilson CI, McNemar, Wilcoxon) fill the leaderboard.

## Quickstart (production)

```bash
docker compose up -d --build
# open http://localhost/ — nginx fronts dashboard + engine socket.io
```

Scale and hardening guidance (engine pooling, sticky sessions, HTTPS,
Postgres migration path, Prometheus scraping): [docs/PRODUCTION.md](docs/PRODUCTION.md).
Full API reference: [docs/API.md](docs/API.md).

## Verification (all green at ship time)

| check | how | status |
|---|---|---|
| research core | `python3 -m pytest recovergraph/tests` | 231 passed |
| engine service | `python3 -m pytest engine/tests` | 10 passed (incl. real sim job) |
| dashboard build | `bun run build` (next build, standalone) | compiles, 13 API routes |
| engine e2e | `make smoke` — boots engine, submits real job, polls to done | PASSED |
| full-stack e2e | `bash scripts/e2e.sh` — register → 401 guard → job → episode DB write-through | PASSED |

## Configuration

Everything is env-driven — see [`.env.example`](.env.example). Highlights:
`ENGINE_URL` (sidecar vs remote engine), `ENGINE_WORKERS` / `ENGINE_MAX_QUEUE`
(concurrency), `MAX_ACTIVE_JOBS_PER_USER` (fairness), `COOKIE_SECURE=1`
(HTTPS deployments), `DATABASE_URL` (SQLite now, Postgres-ready via Prisma).

## License & scope

Research code + production console; hardware experiments are explicitly out
of scope (ADR-006). The novelty claim is a proposed research wedge — see the
honesty constraints in [`recovergraph/docs/spec.md`](recovergraph/docs/spec.md)
and the [literature audit](recovergraph/docs/literature_audit.md).
