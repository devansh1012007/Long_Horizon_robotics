# RecoverGraph: Technical & Execution Specification

Causal, uncertainty-aware recovery for long-horizon robot manipulation.
Version 0.1 (draft for implementation).

## 1. Purpose and scope

**Goal.** Build and evaluate a recovery layer that, when a manipulation action diverges from its expected effect, (a) localizes which action and which cause produced the divergence, (b) determines which downstream task assumptions are now corrupted, and (c) selects the lowest-cost safe recovery action.

**In scope**
- Simulated tabletop pick/place/move manipulation with a single arm.
- Action-effect ledger, divergence detection, Bayesian fault diagnosis, dependency-aware recovery optimization, independent safety supervisor.
- Reproducible fault injection, labelled episode corpus, baseline comparison (incl. LADDER / LADDER-Lite), ablations, optional human-supervision study.

**Out of scope (v1)**
- Real-hardware deployment (stretch goal only, after Phase 6).
- Learning low-level manipulation policies; we reuse scripted skills or existing policies.
- LLM-driven physical diagnosis. LLMs may only (1) parse language into a task graph and (2) verbalize an already computed diagnosis.

**Non-goals / honesty constraints.** The novelty claim is a proposed research wedge, not established. No novelty claim is made in any paper draft until the literature audit (Section 14) is complete.

## 2. Research question, hypotheses, contribution

**RQ.** Does an explicit action-effect ledger plus causal fault diagnosis plus dependency-aware recovery planning improve long-horizon task success and recovery efficiency over (i) retry, (ii) full replanning and (iii) standard verification-based orchestration?

| ID | Hypothesis | Primary evidence |
|---|---|---|
| H1 | Causal recovery achieves higher recovery success than fixed-retry and full-replan baselines under injected faults. | Recovery success rate, paired test |
| H2 | The advantage grows with task horizon (more subgoals). | Success vs. horizon curves |
| H3 | Causal recovery uses fewer extra actions than baselines for equal success. | Extra actions per episode |
| H4 | Unsafe-action rate is less than or equal to baselines. | Unsafe action rate |
| H5 | Causal diagnosis adds value beyond plain state verification (ablation A1). | Ablation delta |
| H6 | Evidence-grounded explanations improve operator intervention quality vs. no explanation or bare error message. | Human study (Phase 7) |

**Contribution statement (candidate).** An uncertainty-aware causal recovery planner that localizes cross-stage execution divergence and selects the lowest-cost safe recovery action using an explicit task-dependency and action-effect model, plus a reproducible fault-injection benchmark with labelled causes.

## 3. Go / no-go criteria

Advance to paper/experiments write-up only if, on the held-out fault suite with at least 30 episodes per cell (Section 10):

1. Recovery success of RecoverGraph exceeds both Retry and Full-Replan baselines, with the paired bootstrap 95% CI of the difference excluding 0.
2. The gap widens at longer horizons (slope of success-vs-horizon differs, CI excludes 0).
3. Mean extra actions is lower than Full-Replan and no higher than Retry.
4. Unsafe-action rate is less than or equal to every baseline (hard requirement: zero hard-constraint violations by the safety supervisor).
5. Ablation A1 (causal diagnosis replaced by plain verification) shows a statistically meaningful drop.

**Reject or redesign** if gains vanish when the planner or perception model is held fixed across systems (i.e. the causal component contributes nothing).

## 4. System architecture

```
NL goal + scene
   -> task_parser -> task_planner (task graph with preconditions/effects/dependencies)
   -> skill_executor (MoveIt 2 + ros2_control)  <-- safety_supervisor (veto, independent)
   -> scene_observer + state_estimator (belief over robot & objects)
   -> effect_verifier (ledger: expected vs observed; divergence score)
   -> fault_diagnoser (posterior over fault hypotheses)
   -> recovery_planner (cost-minimising safe action, dependency invalidation)
   -> verified state update (only after independent physical check)
   -> episode_recorder (all events)
```

**Design principles**
1. Sensor-driven, not LLM-driven: observations and probabilities decide what happened.
2. Verified state update: the task state advances only after an independent check (not the controller success flag).
3. Safety is independent: the safety supervisor runs in its own process, has veto power over every motion command, and its constraints are never traded against cost.
4. Everything logged: every decision is reproducible from the episode log and a seed.

## 5. Data model

All messages are also persisted as JSON so offline analysis does not need ROS.

### 5.1 Task graph

```json
{
  "task_id": "t_0012",
  "goal": "put red cube in bin, then stack blue cube on green cube",
  "subgoals": [
    {"id": "g1", "skill": "pick",  "args": {"obj": "red_cube"},  "pre": ["reachable(red_cube)", "gripper_empty"], "eff": ["holding(red_cube)"]},
    {"id": "g2", "skill": "place", "args": {"obj": "red_cube", "target": "bin"}, "pre": ["holding(red_cube)"], "eff": ["in(red_cube,bin)", "gripper_empty"]}
  ],
  "depends_on": [["g1","g2"], ["g2","g3"]],
  "invariants": ["in(red_cube,bin) must hold until end"]
}
```

`depends_on` is a DAG built with NetworkX. Each edge also records which state variable is carried (e.g. g1 -> g2 carries holding(red_cube)), enabling precise invalidation.

### 5.2 Action-effect ledger entry

```json
{
  "episode_id": "ep_000341", "step": 4, "subgoal": "g2", "skill": "place",
  "preconditions": {"holding(red_cube)": {"predicted": true, "conf": 0.93}},
  "expected_effects": [{"var": "pose(red_cube)", "mean": [0.42,0.10,0.04,0,0,0,1], "cov_diag": [1e-4,1e-4,1e-4,1e-3,1e-3,1e-3]}],
  "predicted_state_conf": 0.88,
  "observed_state": {"pose(red_cube)": {"mean": [0.47,0.10,0.04,0,0,0,1], "cov_diag": [4e-4,4e-4,4e-4,2e-3,2e-3,2e-3]}},
  "actual_effects": ["pose_error=0.05m"],
  "divergence_D": 18.7,
  "downstream": ["g3"],
  "timestamp": 1731800000.12
}
```

### 5.3 Belief state

s_t = (q_t, o_t, g_t, h_t, c_t) where
- q_t: joint positions/velocities + end-effector pose (from robot telemetry; near-deterministic).
- o_t: per-object pose (SE(3) mean + covariance) and identity distribution.
- g_t: gripper width, commanded vs measured force, discrete grasp state in {empty, grasping, holding, lost}.
- h_t: ledger history.
- c_t: per-variable confidence; visibility flags; time since last observation.

### 5.4 Fault hypotheses

F = {f_miss, f_slip, f_collision, f_pose, f_perception, f_controller} plus f_none (no fault). Each hypothesis has a recovery affordance set (Section 6.4).

### 5.5 Episode record (database)

episodes(id, seed, task_id, horizon, system, injected_fault_json, start_state, outcome, wall_time), events(episode_id, t, node, type, payload_json), ledger(...) as 5.2, diagnoses(episode_id, step, posterior_json, argmax, truth), recoveries(episode_id, step, candidates_json, chosen, cost_terms_json, result).

## 6. Algorithms

### 6.1 Prediction

Each skill has a forward model P(s_{t+1} | s_t, a_t) implemented as Gaussian effects around nominal outcomes:
- Pick: holding(obj) with probability p_grasp (from grasp quality heuristic, default 0.9); object pose follows EE.
- Move: EE reaches target within tolerance; held object follows with slip probability p_slip (default 0.03).
- Place: object pose at target with covariance from EE tracking error + grasp offset.

Initial parameters are hand-set, then calibrated from fault-free episodes (Phase 2).

### 6.2 Divergence detection

Residual (Mahalanobis):

D_t = (s_obs - s_hat)^T * Sigma^-1 * (s_obs - s_hat),  Sigma = Sigma_pred + Sigma_obs

Flag divergence if D_t > chi2_inv(1 - alpha, k) with alpha = 0.01 and k = dimension of checked variables. Discrete mismatches (e.g. holding predicted true, observed false) are flagged directly. Use separate thresholds per variable group (object pose, gripper state) to reduce false alarms; tune to a false-alarm rate under 2% on fault-free episodes.

### 6.3 Causal fault diagnosis (Bayesian, interpretable first)

P(f_i | e_t, a_t, h_t) ∝ P(e_t | f_i, a_t) * P(f_i | a_t, h_t)

Evidence vector e_t (all from sensors):

| Feature | Source |
|---|---|
| gripper width at close vs expected object width | gripper telemetry |
| grip force profile during lift (drop events) | wrench / finger effort |
| object pose relative to EE over time (drift) | tracker |
| object visibility / occlusion fraction | camera mask |
| EE pose error at end of motion | telemetry vs commanded |
| controller status (timeout, abort, collision flag) | ros2_control / MoveIt |
| scene diff on non-target objects | scene_observer |
| perception confidence / detection flicker | detector |

Likelihood table (initial hand-set, then calibrated). Values are P(evidence observed | fault):

| Evidence | f_none | f_miss | f_slip | f_pose | f_collision | f_perception | f_controller |
|---|---|---|---|---|---|---|---|
| Gripper closed to near-zero width | 0.02 | 0.90 | 0.05 | 0.05 | 0.10 | 0.05 | 0.05 |
| Grip force drop mid-transport | 0.02 | 0.02 | 0.90 | 0.05 | 0.30 | 0.02 | 0.05 |
| Object stays in EE frame but final pose off | 0.03 | 0.02 | 0.05 | 0.90 | 0.15 | 0.20 | 0.10 |
| Object low visibility, EE pose nominal | 0.02 | 0.05 | 0.05 | 0.02 | 0.02 | 0.90 | 0.02 |
| Controller timeout / abort flag | 0.01 | 0.05 | 0.05 | 0.10 | 0.30 | 0.02 | 0.95 |
| Non-target object moved | 0.01 | 0.02 | 0.05 | 0.05 | 0.90 | 0.05 | 0.05 |

Treat features as conditionally independent given the fault (naive Bayes) in v1. Priors depend on the skill (e.g. pick: miss 0.4 of failures; move: slip 0.4). Output: posterior vector, entropy, and top-2 gap.

**Disambiguating actions.** If top-2 gap < 0.2 or entropy > 1.2 nats, the diagnoser requests a cheap discriminating action (re-observe from another viewpoint, gentle gripper squeeze test) before committing to an expensive recovery. Expected information gain per candidate probe is computed from the likelihood table.

**Phase-4b upgrade (optional).** Replace naive Bayes with a small Bayesian network, then a learned likelihood model (PyTorch, calibrated with temperature scaling) trained on injected-fault episodes. Report both versions.

### 6.4 Recovery candidates and affordances

| Fault | Sensible recoveries | Notes |
|---|---|---|
| f_miss | regrasp (new pose), re-observe then retry with modified params | plain retry of the same grasp is disallowed until cause resolved |
| f_slip | re-observe, regrasp, retry with higher force or slower trajectory, rollback if object fell somewhere recoverable | |
| f_pose | correction move, regrasp and replace | |
| f_collision | stop, re-observe scene, replan; rollback only if safe | safety supervisor veto |
| f_perception | re-observe (new view), re-detect; no physical action | |
| f_controller | retry with modified timing/planner params; replan motion | |
| any, high risk | escalate to human | |

### 6.5 Dependency-aware invalidation

Given the divergent step g_k and the task DAG:
1. Mark the state variables corrupted by the diagnosed fault (e.g. holding(red_cube) false after f_miss).
2. Traverse descendants through edges that carry corrupted variables; mark them stale.
3. Stale subgoals must have preconditions re-verified before execution; non-stale subgoals are retained (this is the key difference from full re-planning).
4. Check invariants (e.g. previously placed objects) and re-observe if the fault could have disturbed them (f_collision).

### 6.6 Recovery optimization

r* = argmin_{r in R_feasible} alpha*T(r) + beta*C(r) + gamma*P(fail | r) + delta*H(r)
     s.t.  P(unsafe | r) <= epsilon,  precondition(r) satisfied

Expected cost is taken over the diagnosis posterior: E_f[...] = sum_i P(f_i) * cost(r | f_i).

| Term | Default | Estimation |
|---|---|---|
| T(r) seconds | alpha = 1.0 | profiled per recovery type, scaled by remaining path |
| C(r) energy/extra actions | beta = 0.5 | action count and joint-travel |
| P(fail \| r) | gamma = 20 | learned/hand table per fault x recovery |
| H(r) intervention | delta = 60 for escalate, 0 otherwise | operator time estimate |
| epsilon | 0.01 | hard constraint |

If no feasible recovery meets epsilon, or all expected costs exceed the escalation cost, escalate with an evidence package (Section 9).

A loop guard prevents repeated identical recoveries: after two failed recoveries of the same type for the same fault, that type is removed from R_feasible for the subgoal.

### 6.7 Verified state update

Task state advances only if effect_verifier confirms the expected effect with posterior confidence >= 0.9 from an independent signal (e.g. camera pose agrees with EE-based prediction). Otherwise the subgoal is marked unverified and triggers re-observe.

### 6.8 Safety supervisor

Separate process. Checks every trajectory before execution: joint/velocity/torque limits, workspace bounds, self- and scene-collision (MoveIt planning scene, with inflated margins), and speed limits near humans (if modelled). Returns ACCEPT / REJECT with reason. Its decisions are logged and counted in unsafe action attempts (proposed-but-vetoed) and unsafe executions (should be zero).

## 7. ROS 2 interface specification

| Node | Subscribes | Publishes | Services / Actions |
|---|---|---|---|
| task_parser | none | /task/goal (TaskGoal) | service /parse_instruction |
| task_planner | /task/goal | /task/graph (TaskGraph) | service /replan (stale set in, patched graph out) |
| scene_observer | camera topics | /scene/objects (ObjectEstimateArray) | service /reobserve (viewpoint hint) |
| state_estimator | /scene/objects, /joint_states, /gripper/state | /belief/state (BeliefState) | none |
| skill_executor | /task/graph, /safety/verdict | /exec/events | action /skill/execute (SkillGoal -> SkillResult) |
| effect_verifier | /belief/state, /exec/events | /ledger/entry (LedgerEntry) | service /verify_effect |
| fault_diagnoser | /ledger/entry, telemetry | /diagnosis (Posterior) | service /probe_suggestion |
| recovery_planner | /diagnosis, /task/graph | /recovery/decision | action /recovery/execute |
| safety_supervisor | /planned_trajectory | /safety/verdict | service /check_trajectory |
| episode_recorder | all of the above | none | writes DB and rosbag2 |

**QoS.** Telemetry and observations: SensorDataQoS. Ledger, diagnosis, decisions: reliable + transient local. Long skills use actions with feedback and cancel; discrete queries use services.

**Timing targets.** Observer and estimator at 10-30 Hz; ledger/diagnosis computation under 100 ms per step; recovery planning under 500 ms (excluding motion planning).

**Determinism.** Fixed seeds, use_sim_time true, and logged rosbag2 per episode so any episode can be replayed offline.

## 8. Simulation environment

Recommended primary: MuJoCo (fast, deterministic, easy contact/fault injection, easy parallel batch runs) with a ROS 2 bridge. Keep a Gazebo Harmonic + MoveIt 2 configuration as a secondary check only if time allows. Decision to confirm in Phase 0 after inspecting what LADDER requires; if LADDER needs a specific simulator, match it for the baseline runs.

- Robot: Franka Panda (or whichever arm LADDER uses) with parallel gripper.
- Scene: table, 3-8 objects (cubes, cylinders, bin, plate), one overhead and one angled camera, AprilTags on table for calibration.
- Perception: ground-truth poses plus a noise model (Gaussian pose noise, dropout, occlusion masks) for most experiments; optional OpenCV/AprilTag + pose estimator in a perception-realism ablation.
- Task suites: horizons of 1, 2, 3, 5, 7 and 10 subgoals, 20 distinct task templates per horizon, generated with randomized object placements.

## 9. Human-supervision output

When escalating, the system emits a structured report:

```json
{
  "failure_reason": "Object slipped during transport (p=0.78)",
  "evidence": ["grip force dropped 62% at t=3.2s", "object lost from EE frame"],
  "alternatives": [{"action":"regrasp","time_s":14,"risk":0.02},{"action":"rollback","time_s":30,"risk":0.01}],
  "stale_subgoals": ["g3","g4"],
  "why_escalating": "all safe options exceed autonomy risk threshold"
}
```

Rendered in a React dashboard (FastAPI backend) with the evidence timeline and a one-click approve/choose-alternative control.

## 10. Fault injection and experiment design

### 10.1 Fault injection spec

| Fault | Injection | Parameters (sampled per episode) | Ground-truth label |
|---|---|---|---|
| Failed grasp | Offset grasp target | offset 2-6 cm lateral | f_miss |
| Slippage | Reduce friction or apply disturbance force during transport | friction x0.3-0.6, or pulse 5-15 N, 50-150 ms | f_slip |
| Placement error | Perturb EE pose before release | 2-8 cm, 5-20 deg | f_pose |
| Occlusion | Mask a camera region | 30-80% of object visible area for 1-3 s | f_perception |
| Object displacement | Move object mid-execution | 3-15 cm shift | f_collision / displaced (label as displaced) |
| Controller failure | Force timeout or abort | at random point in trajectory | f_controller |
| Dependency failure | Change an order-sensitive subgoal state | flips a precondition of a later subgoal | stale dependency |

Each fault is injected at a random step (stratified: early/middle/late) with a logged seed. Also run fault-free episodes (false-alarm rate) and compound episodes (two faults) as a stress test.

### 10.2 Dataset splits

- Calibration set: fault-free + single-fault, used to fit the forward model and likelihoods.
- Validation set: tune cost weights, thresholds.
- Test set: held-out seeds and held-out task templates; touched once.

Target size: 7 faults x 6 horizons x 30 episodes = 1,260 test episodes per system, plus calibration/validation (~3,000 episodes). Batch run with Docker Compose and parallel simulators.

### 10.3 Systems compared

| ID | System |
|---|---|
| B1 | Low-level policy/skills without recovery |
| B2 | Fixed retry (up to 3) |
| B3 | Full replanning from current state |
| B4 | Verification-based orchestration (e.g. LADDER-style verifier) |
| B5 | Fault-pipeline baseline (pose/wrench error detection + forward/backward recovery) where reproducible |
| RG | RecoverGraph (full) |

Controlled variables: same skills, same perception noise, same planner, same seeds across systems so differences are attributable to recovery logic.

### 10.4 Ablations

| ID | Change | Tests |
|---|---|---|
| A1 | Replace causal diagnosis with plain verification (success/fail only) | H5 |
| A2 | Remove dependency invalidation (replan everything) | value of task-dependency model |
| A3 | Remove uncertainty (point estimates) | value of uncertainty |
| A4 | Remove cost-based selection (fixed priority ordering) | value of optimization |
| A5 | Hand-set vs learned likelihoods | learning benefit |
| A6 | Noisy vs ground-truth perception | robustness |

### 10.5 Metrics (formal definitions)

| Metric | Definition |
|---|---|
| End-to-end success | fraction of episodes completing the goal within step/time budget |
| Subgoal success | per-subgoal verified completion rate |
| Recovery success | fraction of fault episodes that complete after recovery |
| Recovery time | seconds from divergence detection to restored verified state |
| Extra actions | total actions minus nominal plan length |
| Unsafe action rate | safety-supervisor vetoes per 100 actions; plus unsafe executions (target 0) |
| Intervention count | human escalations per episode |
| Diagnosis accuracy | argmax posterior = injected label; plus per-class confusion matrix and macro-F1 |
| Recovery regret | cost of chosen recovery minus cost of best feasible recovery (computed by oracle that knows the true fault) |
| Calibration | expected calibration error and Brier score of posteriors; reliability diagram |
| False-alarm rate | divergence flagged on fault-free steps |

### 10.6 Statistics

Paired comparison by seed; paired bootstrap (10,000 resamples) for 95% CIs; Wilcoxon signed-rank or McNemar for success differences; Holm-Bonferroni correction across ablations. Report results per horizon, never only pooled. Pre-register hypotheses, metrics and the test split before running test experiments.

## 11. Human study (Phase 7, optional but planned)

- Design: within-subject, three conditions: no explanation, basic error message, evidence-grounded explanation (Latin-square order).
- Participants: 12-20, with ethics approval from the institution before recruiting.
- Task: respond to 6 pre-scripted failure scenarios per condition.
- Measures: decision correctness, time to decision, NASA-TLX workload, trust/clarity Likert items.
- Analysis: mixed-effects model with participant random intercept.

## 12. Implementation plan (execution)

Indicative timeline: ~26 weeks for one primary developer; adjust to your semester/schedule.

| Phase | Weeks | Work | Deliverable | Acceptance test |
|---|---|---|---|---|
| 0. Baseline reproduction | 1-3 | Clone LADDER, read task format and baseline adapters, run LADDER-Lite, log episodes; settle simulator choice; start literature audit (Section 14) | Working baseline with logged episodes | Reproduces reported trend (success falls with subgoal count) within reasonable tolerance; results table committed |
| 1. Deterministic stack | 4-7 | Tabletop env, pick/place/move/observe skills, task graph + executor, MoveIt 2 motion planning, ros2_control | Reliable multi-step execution without recovery | >= 95% success on 1-3 subgoal tasks fault-free over 200 episodes |
| 2. Action-effect ledger | 8-10 | Declare preconditions/effects per skill; forward models; observer; ledger writer; divergence detector; calibration | Ledger + divergence detector | False-alarm < 2% fault-free; detects >= 90% of injected faults at any step |
| 3. Fault injection | 11-12 | Injection module for the 7 fault types, seeding, labelling, DB logging | Fault-injection environment + labelled dataset | Every injected fault reproducible from seed; labels verified on a manual sample of 100 episodes |
| 4. Causal diagnosis | 13-15 | Naive Bayes diagnosis, probe suggestion, entropy gate; confusion matrix; calibration plots | Diagnosis model | Macro-F1 >= 0.80 on calibration/validation; ECE < 0.10; faults needing different recoveries are separable (report which are not) |
| 5. Recovery optimization | 16-19 | Candidate generator, cost model, dependency invalidation, loop guard, safety supervisor integration, escalation report | Functioning recovery policy | Beats B2 and B3 on validation set; zero unsafe executions |
| 6. Research experiments | 20-23 | Run B1-B5, RG, ablations, horizon sweeps, compound faults; statistics | Results + reproducible logs | Go/no-go table (Section 3) completed |
| 7. Human study | 24-26 | Dashboard, scenarios, ethics, study, analysis | HRI results | Pre-registered analysis completed |

**Weekly cadence.** Each week ends with: runnable demo, updated experiment log, short written note (what changed, what failed, next week's plan). Tag a Git release per phase.

**Decision gates.** End of Phase 0: confirm LADDER runs and simulator choice. End of Phase 4: if diagnosis cannot separate fault classes better than chance beyond verification, redesign features before building the optimizer. End of Phase 5: quick sanity win over retry, else debug before spending compute on Phase 6.

## 13. Repository, tooling and reproducibility

```
recovergraph/
  docs/                  # this spec, ADRs, paper drafts
  ros2_ws/src/
    rg_msgs/             # msg/srv/action definitions
    rg_task/             # parser, planner
    rg_exec/             # skill_executor, safety_supervisor
    rg_perception/       # scene_observer, state_estimator
    rg_recovery/         # effect_verifier, fault_diagnoser, recovery_planner
    rg_sim/              # MuJoCo/Gazebo env, fault injector
  rg_core/               # pure-Python algorithms (testable without ROS)
  experiments/           # configs, run scripts, analysis notebooks
  backend/ dashboard/    # FastAPI, React
  docker/                # images, compose files
  tests/                 # unit, integration, replay tests
```

- Stack: ROS 2 Jazzy, C++ (controllers), Python (algorithms), MoveIt 2, ros2_control, NetworkX, PyTorch, PostgreSQL (events), Redis (job queue), FastAPI, React, Docker.
- Testing: unit tests for rg_core (ledger math, Bayes updates, cost selection) with property tests; integration tests in simulation; replay tests that feed recorded rosbags through the diagnoser to confirm identical outputs.
- CI: GitHub Actions running lint, unit tests, and a 20-episode smoke simulation.
- Experiment tracking: config files per run (YAML), git commit hash, seed list, and environment digest stored with results; results tables auto-generated from the DB.
- Compute: CPU-only is sufficient for the Bayesian version; a single GPU is only needed for the learned-likelihood (A5) and any pose estimator.

## 14. Literature audit (must complete before any novelty claim)

Search and record in a structured table (paper, venue/year, what it does, closest overlap, difference):
1. Robot failure detection and recovery (fault event pipelines, pose and wrench error detection, forward/backward recovery with MPIC).
2. Long-horizon manipulation benchmarks and failure analyses (LADDER and its baselines).
3. Task-and-motion planning with failure handling, replanning, and execution monitoring.
4. Causal reasoning for robotics and causal failure explanation.
5. Uncertainty-aware / belief-space planning and POMDP-based recovery.
6. Behavior trees / PDDL execution monitors with recovery.
7. LLM/VLM-based failure reasoning and recovery for robots (the closest competitors; many recent papers).
8. Human-robot interaction on failure explanations.

Exit criterion: a one-page related-work matrix and a written statement of exactly what is and is not new.

## 15. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| LADDER integration harder than expected | Medium | Phase 0 gate; fall back to own task suite with LADDER-style horizons and cite as inspired-by |
| Causal diagnosis adds nothing over verification | Medium | Ablation A1 built in; go/no-go rule; pivot to a negative-result/analysis paper |
| Simulation too clean; results do not transfer | Medium | Noise models, A6 ablation; optional hardware demo as stretch |
| Hand-set likelihoods are arbitrary | High | Calibrate on calibration set; report sensitivity analysis; learned variant A5 |
| Fault labels ambiguous (e.g. slip vs pose error) | Medium | Define label by injected mechanism; report confusion honestly; allow multi-label in compound tests |
| Scope creep (dashboard, human study) | High | Core claims need only Phases 0-6; Phase 7 is optional |
| Safety constraint implemented weakly | Low-Medium | Independent process; adversarial tests that propose unsafe motions and verify vetoes |
| Compute/time overruns | Medium | Parallel batch runs; start with reduced N, scale after pipeline is stable |

## 16. Open decisions

1. Simulator: MuJoCo vs Gazebo (depends on LADDER requirements).
2. Robot model and gripper (match LADDER where possible).
3. Whether the first paper includes the human study or defers it.
4. Compute budget and available GPU.
5. Target venue (e.g. a robotics conference/journal workshop first, full paper after results) and submission deadline, which fixes the timeline.
6. Whether to attempt the real-hardware stretch demo.

## 17. Immediate next steps (first 7 days)

1. Create the repo and Docker environment with ROS 2 Jazzy; commit this spec under docs/.
2. Clone LADDER, run LADDER-Lite, and write down task format, adapter interface and simulator dependency.
3. Draft the literature-audit table skeleton and fill the first 15 papers.
4. Define rg_msgs (TaskGraph, LedgerEntry, BeliefState, Posterior, RecoveryDecision).
5. Write rg_core unit tests for the divergence statistic and the Bayes update before implementing them.
6. Fix the pre-registration document: hypotheses H1-H6, metrics, splits, and stats plan.
