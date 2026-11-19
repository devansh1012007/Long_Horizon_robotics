# RecoverGraph

Causal, uncertainty-aware recovery for long-horizon robot manipulation —
**milestone 1 implementation** of the [RecoverGraph spec](docs/spec.md)
(v0.1). When a manipulation action diverges from its expected effect, the
system localizes *which action and cause* produced the divergence, marks
*which downstream task assumptions are corrupted*, and selects the
*lowest-cost safe recovery* — with an independent safety supervisor and a
verified state update.

> **Honesty constraint (spec 1, 14):** the novelty claim is a proposed
> research wedge, not an established result. No novelty claim is made until
> the [literature audit](docs/literature_audit.md) is complete. Smoke-run
> numbers below are reduced-N sanity checks, **not** the pre-registered test
> results (see [pre-registration](docs/pre_registration.md)).

## What is implemented (milestone 1)

| Spec element | Where | State |
|---|---|---|
| Divergence detection (6.2) | `rg_core/divergence.py` | Mahalanobis D, chi2 thresholds (stdlib impl, scipy-verified), Bonferroni per step; FA < 2% on fault-free steps measured 0.7% |
| Bayes diagnosis + probes (6.3) | `rg_core/diagnosis.py` | Likelihood table verbatim from spec (test-guarded), skill priors, entropy/top-2-gate, information-gain probes |
| Recovery optimization (6.4-6.6) | `rg_core/recovery.py` | Affordances, cost model (alpha/beta/gamma/delta/epsilon), loop guard, Section 9 escalation reports |
| Dependency invalidation (6.5) | `rg_core/task_graph.py` | Carried-variable DAG traversal, conservative fixpoint, invariant checks |
| Verified state update (6.7) | `rgsim/pipeline.py` | camera-only confidence = chi2_sf(D_tol, k) >= 0.9, one re-observe, then divergence handling |
| Independent safety supervisor (6.8) | `rg_core/safety.py` + ROS node | workspace/speed/collision-margin vetoes; vetoed motions never execute; unsafe executions = 0 by construction and test |
| Fault injection (10.1) | `rgsim/fault_injector.py` | 7 mechanisms, seeded + stratified, skill-compatibility, labels follow injected mechanism |
| Systems B1-B5, RG, A1-A5 (10.3-10.4) | `systems/` | B4 = LADDER-style verification orchestrator (per spec description); A5 loader falls back honestly if no learned artifact |
| Statistics (10.6) | `experiments/stats.py` | paired bootstrap 10k, McNemar exact, Wilcoxon, Holm-Bonferroni, Wilson CIs |
| Episode DB (5.5) | `rg_core/episode_db.py` | SQLite: episodes/events/ledger/diagnoses/recoveries |
| ROS 2 Jazzy scaffold (7) | `ros2_ws/src/` | 6 packages, 21 typed interfaces, nodes wrapping rg_core, bringup launch; **runs on your machine** (`docker/Dockerfile.ros`), validated structurally here |

**Completed since milestone 1 (later phases of the same spec):** MuJoCo contact
sim behind an interface-preserving backend factory (`rgsim/backends.py`, ADR-001),
frozen-parameter calibration + pre-registered formal evaluation on both backends
(`scripts/calibrate.py`, `scripts/formal_eval.py`), LADDER-aligned external-validity
study (`scripts/ladder_study.py`), learned-likelihood artifact with honest fallback
(`rg_core/learning.py`, A5), G2/G3 quality gates, and the multi-user production
dashboard + engine service living at the repository root (see the root README).
**Still out of scope:** human study (Phase 7), hardware (ADR-006).

## Quickstart

```bash
pip install -e ".[dev]"
python -m pytest tests                    # 231 tests
python scripts/demo.py                    # 4-scenario demo + escalation JSON
python scripts/check_ros_scaffold.py      # structural ROS validation

# smoke batch (10 systems x 4 horizons x 3 conditions x 12 seeds = 1440 eps, ~5 s)
python -m experiments.run_episodes --systems B1 B2 B3 B4 B5 RG A1 A2 A3 A4 \
    --horizons 1 2 3 5 --conditions single none compound --episodes 12 \
    --out experiments/results/smoke
python -m experiments.analysis --results experiments/results/smoke
```

### ROS 2 (on a machine with ROS 2 Jazzy, or via Docker)

```bash
docker build -f docker/Dockerfile.ros -t recovergraph-ros .
docker run --rm -it recovergraph-ros        # brings up all nodes
# or locally:
. /opt/ros/jazzy/setup.sh && cd ros2_ws && colcon build && \
  . install/setup.sh && ros2 launch rg_sim bringup.launch.py
```

## Repository map (spec 13, milestone-1 realisation)

```
rg_core/      pure-Python algorithms: types, task graph, forward models,
              divergence, diagnosis+probes, recovery optimizer, safety, DB,
              learning (A5 artifact)
rgsim/        world (kinematic), mujoco_world (physics), backends.py factory,
              skills, noise, 7-fault injector, pipeline
systems/      B1-B5, RG, ablations A1-A6 (registry)
experiments/  batch runner, statistics, analysis, plots, results/
tests/        231 unit/integration tests (divergence + Bayes tests written
              FIRST, per spec 17.5)
ros2_ws/src/  rg_msgs + rg_task + rg_exec + rg_perception + rg_recovery + rg_sim
scripts/      demo, formal_eval, calibrate, ladder_study, g2_gate, ros_e2e, ...
docker/       Dockerfile.core, Dockerfile.ros, docker-compose.yml
docs/         spec.md (committed verbatim), pre_registration.md, adr/,
              literature_audit.md, paper_draft.md
```

## Milestone-1 smoke results (reduced N; NOT the test set)

From `experiments/results/smoke/summary.md` (1,440 episodes, paired seeds):

- RG vs B1/B2/B3/B4: **+0.73 / +0.31 / +0.33 / +0.38** success (paired
  bootstrap CIs exclude 0; McNemar p <= 1.5e-3).
- RG vs B5: +0.04 (CI touches 0) — the classic threshold pipeline is a strong
  baseline; RG's edge shows on compound faults (8/12 vs 7/12) and in
  diagnosis/calibration/regret, which B5 does not provide.
- Diagnosis accuracy **1.00** on 246 scored manifesting episodes (smoke seeds);
  false-alarm rate **0.7%** (< 2% target); mean recovery regret 1.64 cost units.
- Ablation deltas at this N are small (A1 +0.08 n.s.) — the full pre-registered
  suite (>= 30 episodes/cell) is required before any claim.

Known simplifications (documented in code): naive-Bayes likelihoods are the
hand-set spec tables; recovery macro costs are profiled nominal values;
`stale_dependency` faults are excluded from diagnosis scoring; escalations
are rare under milestone-1 parameters (the demo forces the loop-guard
exhaustion to exercise the Section 9 path).

## Reproducibility

Every episode is a function of `(system, seed, task template, condition)`:
same seed -> identical metrics (enforced by `tests/test_pipeline_smoke.py::TestDeterminism`).
Runs write `run_meta.json` (config + wall time) next to `results.csv` and the
SQLite DB. CI (`.github/workflows/ci.yml`) runs the unit suite, the ROS
scaffold check, and a 20-episode smoke batch on every push.

## Milestone mapping to spec Section 17 ("first 7 days")

| Step | Status |
|---|---|
| 1. Repo + Docker + ROS 2 Jazzy env; spec committed under docs/ | done (`docker/`, `docs/spec.md`) |
| 2. Clone LADDER, task format + adapter interface | **open** — Phase 0 gate; B4 implements the spec's description of a verification orchestrator as a stand-in |
| 3. Literature-audit table skeleton + first 15 papers | done via web search (`docs/literature_audit.md`) — LADDER verification status recorded honestly there |
| 4. rg_msgs (TaskGraph, LedgerEntry, BeliefState, Posterior, RecoveryDecision) | done (`ros2_ws/src/rg_msgs/`) |
| 5. rg_core unit tests for divergence statistic + Bayes update BEFORE implementation | done (tests-first, red→green) |
| 6. Pre-registration document | done (`docs/pre_registration.md`) |


## Program completion (M2-M7, 2026-11-18)

All six stages of the full-application plan completed; 242-test suite green.

- **M2 Phase 0 gate (G1)**: LADDER audited (`docs/LADDER_ADAPTER.md`,
  fallback + structural adoption); ADR-001 **Accepted** (MuJoCo 4.1 CPU-only);
  tuning frozen (`configs/tuning_freeze_v1.json`, tag `v1.0.0-m1`).
- **M3 physics backend (G2 PASS)**: `rgsim/mujoco_world.py` +
  `mujoco_skills.py` behind the same interface; 7 mechanisms relocated to
  contact level; fault-free 1.00 / FA 0.74% / detection 0.985
  manifest-conditioned; safety adversarial tests at contact level.
- **M4 ROS 2 end-to-end**: in-process rclpy/rg_msgs bus shim
  (`ros2_ws/shim/`); six packages wired (scripts/ros_e2e.py); 100-episode
  reconciliation vs the in-process harness; bag replay determinism 6/6;
  safety veto on the live message flow; decision-path latency p95 0.41 ms.
- **M5 learning + calibration (G3 PASS)**: `rg_core/learning.py` episode-DB
  fitting with selection-bias fixes; learned artifact v1
  (`configs/learned_artifact_v1.json`); validation tuning frozen
  (`configs/tuning_v2.json`: alpha 0.002, delta 40); macro-F1 0.893 / ECE
  0.062 on validation (calibrated).
- **M6 formal pre-registered evaluation (4/5 criteria)**: test split touched
  once on frozen tag `v1.4.0-m6-freeze`; 30,240 rows on both backends;
  RG 0.788 vs B5 0.615 / B3 0.473 / B2 0.374 (all paired CIs exclude 0,
  including +0.173 over B5); H2 slope CIs exclude zero; zero unsafe; A1 drop
  +0.168 (Holm p~0); criterion 3 (extra actions <= B2) FAILED honestly.
  Archive: `experiments/results/formal/` (go_no_go.json, tables.md,
  archive.json, figures).
- **M7**: paper draft `docs/paper_draft.md`; LADDER-aligned external
  validity (`scripts/ladder_study.py`); this package.

Gates: G1 PASS, G2 PASS, G3 PASS, G4 4-of-5 (criterion 3 failed as
measured; the pre-registered fallback for *vanishing gains* does not apply).
