# API reference

Two layers: the **gateway** (Next.js, `src/app/api/**`, cookie-authenticated,
rate-limited) is what browsers and scripts talk to; the **engine**
(FastAPI, `engine/index.py`) is an internal service the gateway proxies to.
Unless stated otherwise, requests and responses are JSON.

Rate limit rules (token bucket, per user): auth 15/5 min, submit 20/min,
read 240/min, heavy 60/min. Exceeding → `429` with `Retry-After`.

## Gateway

### Auth
| method & path | body | notes |
|---|---|---|
| POST `/api/auth/register` | `{name, email, password}` | password ≥ 8 chars; sets `rg_session` HttpOnly cookie |
| POST `/api/auth/login` | `{email, password}` | scrypt-verified; sets session cookie |
| POST `/api/auth/logout` | — | deletes session row + cookie |
| GET `/api/auth/me` | — | `{user}` or `{user: null}` |

### Jobs
| method & path | body / query | notes |
|---|---|---|
| POST `/api/jobs` | `{systems[], horizons[], condition, fault?, episodes, base_seed?, label?}` | caps: 1–12 systems, horizons ⊂ [1,2,3,5,8], 1–100 episodes, total ≤ 600; per-user quota `MAX_ACTIVE_JOBS_PER_USER` (default 2) → `429`; returns `{job_id, status, total}` |
| GET `/api/jobs` | — | owned jobs; live-syncs with the engine (write-through to Prisma) |
| GET `/api/jobs/{id}` | — | owned only (`404` otherwise); on completion fetches full result and persists episode rows |
| DELETE `/api/jobs/{id}` | — | blocked while queued/running (`409`); removes engine artifacts + Prisma copy |
| GET `/api/jobs/{id}/result` | — | full result incl. per-episode rows (proxied) |
| GET `/api/jobs/{id}/episodes/{ep}` | — | episode drill-down: events, diagnoses, recoveries (proxied) |

### Analytics & ops
| method & path | query | notes |
|---|---|---|
| GET `/api/episodes` | `system, fault, outcome, jobId, page, pageSize` | paginated episode index (`{total, page, pageSize, rows[]}`) from the Prisma write-through copy |
| GET `/api/leaderboard` | — | per-system aggregates (success rate, Wilson CI, extra actions, diagnosis accuracy) over the user's episodes |
| GET `/api/ops` | — | engine health + sidecar status + platform counters |

Error shape: `{ "error": "message" }` with 400/401/404/409/429/500/503.

## Engine (internal, default `127.0.0.1:3031`)

| method & path | notes |
|---|---|
| GET `/health` | version, uptime, worker pool (`workers/active/queued`), jobs by status, lifetime totals, limits |
| GET `/metrics` | Prometheus text: `rg_engine_up`, `rg_engine_uptime_seconds`, `rg_engine_jobs_total{state}`, `rg_engine_episodes_total`, `rg_engine_active_jobs`, `rg_engine_queued_jobs`, `rg_engine_jobs_by_status{status}` |
| GET `/capabilities` | systems (B1–B5, RG, A1–A6) with descriptions, faults + labels, conditions, horizons, limits |
| POST `/jobs` | `{user_id, spec}` — spec mirrors the gateway plus `backend: "kinematic" | "mujoco"`; validates systems/condition/fault/horizons/backend and size caps (`400`), MuJoCo uninstalled (`503`), queue full (`503`) |
| GET `/jobs?user_id=&limit=` | compact job list (no rows) |
| GET `/jobs/{id}` | status/progress/summary/stats |
| GET `/jobs/{id}/result` | full payload incl. `rows[]`; `409` until done |
| GET `/jobs/{id}/episodes/{ep}` | episode record + last 500 events + diagnoses + recoveries from the per-job SQLite DB |
| DELETE `/jobs/{id}` | drops artifacts (`.json` + `.sqlite`); blocked while queued/running |

### Job result payload
`summary[system]` = `{n, successes, success_rate, ci_lo, ci_hi,
extra_actions_mean, recovery_time_mean, diag_n, diag_accuracy, escalations,
interventions, unsafe_executions, false_alarm_episode_rate}` (Wilson 95% CI).
`stats[]` = RG vs each co-submitted system, paired by (horizon, seed):
`{vs, n_pairs, delta_success_mean, delta_ci_lo, delta_ci_hi,
ci_excludes_zero, mcnemar_p, wilcoxon_p}` — the pre-registered statistics
(paired bootstrap 10k, McNemar exact, Wilcoxon signed-rank).
`rows[]` = one record per episode: outcome, success, extra_actions,
recovery_time_s, vetoes, unsafe_executions, interventions, escalations,
flags, flags_fault_free, steps, diag_correct, regret, fault, backend, task_id.

### socket.io (path `/socket.io/`, engine.io v4)
- `emit("join", room)` with room `"global"` or `job_<id>`; `emit("leave", room)`.
  Late joiners receive the job snapshot plus a buffered telemetry tail.
- `job:update` — `{id, status, progress, total, ...}`.
- `ep:event` — `{job, etype, ep, ...}` with `etype` ∈ `divergence`
  (`D`, `fault_free`), `diagnosis` (`argmax`, `truth`, `entropy`, top-3
  posterior), `recovery` (`chosen`, `result`, `cost`), `outcome`
  (`success`, `extra_actions`, `recovery_time_s`, `escalations`, ...), plus
  throttled per-step events. Non-outcome events are capped per episode to
  protect the channel.
