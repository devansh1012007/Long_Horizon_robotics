# RecoverGraph: Causal, Uncertainty-Aware Recovery for Long-Horizon Robot Manipulation

**Draft v1 (M7).** Pre-registered claims evaluated on the held-out test split
(2026-11-18, tag v1.4.0-m6-freeze). All numbers below come from the archived
result tables (`experiments/results/formal/go_no_go.json`); smoke and
validation numbers are labelled as such wherever shown.

## Abstract

Long-horizon manipulation fails not because planners cannot re-plan, but
because recovery after a mid-execution fault is blind: it does not know
*what* went wrong, *which* downstream assumptions died with it, or *what the
cheapest safe repair* is. RecoverGraph is a recovery layer that treats
recovery as a causal inference problem: a per-step divergence test over
predicted-vs-observed effects, an interpretable Bayesian fault diagnosis
over seven mechanisms, dependency-aware invalidation of task-graph
assumptions, and cost-minimising recovery selection under a hard,
independent safety constraint. On a pre-registered, held-out test
population (7 fault mechanisms x 6 horizons x 30 episodes, 12 systems, two
simulator backends: a fast kinematic world and a MuJoCo contact world),
RecoverGraph recovers 78.8% of fault episodes versus 47.3% for full
re-planning, 37.4% for blind retry and 61.5% for a classic threshold-based
fault pipeline, with paired 95% CIs excluding zero for every comparison -
including the strong fault-pipeline baseline (+0.173, CI [0.156, 0.190]).
The advantage widens with horizon (bootstrap slope differences exclude
zero), zero unsafe executions occur across 30,240 episodes, and removing
the causal diagnosis (ablation A1) costs 16.8 percentage points
(Holm-adjusted p ~ 0). The pre-registered criterion that RecoverGraph use
no more extra actions than blind retry *fails*: causal recovery costs 5.39
extra actions per episode versus 4.08 for retry (2.94 vs 4.48 against full
re-planning) - we report this as measured, and as the price of diagnosing
before acting. A calibrated likelihood table fitted on a never-scored
split improves diagnosis macro-F1 from 0.814 to 0.856 and expected
calibration error from 0.168 to 0.095 on the test split.

## 1. Introduction

Robustness work on long-horizon manipulation has converged on verification
and re-planning: verify each subgoal's effect and re-plan when a check
fails (LADDER's verifier methods; verification-based orchestrators). These
approaches detect *that* an effect failed, not *why*, and therefore cannot
choose a repair that addresses the cause, cannot tell which downstream
assumptions were invalidated, and burn action budgets on retries of
mechanisms that will fail again.

RecoverGraph makes three contributions:

1. **Causal recovery as an explicit inference-and-optimisation loop**
   (Bayes diagnosis over seven mechanisms with ambiguity-gated probes;
   affordance-constrained, cost-minimising recovery selection; loop guard
   and structured escalation).
2. **A pre-registered, dual-backend evaluation** that isolates the causal
   layer from confounds (identical skills, perception noise, planner and
   seeds across all systems; recovery logic is the only difference), run
   once on frozen code.
3. **Honest negative results**, reported as measured: the extra-action
   criterion failure (Section 5), the point-estimate ablation outperforming
   the full uncertainty model (A3), and the collapse of the verified-state
   gate under doubled perception noise (A6).

## 2. Related work

The literature audit (docs/literature_audit.md) anchors novelty: LADDER
(arXiv 2610.18053) is the long-horizon benchmark (145 tasks, 1-10 subgoals,
three suites on Isaac Sim/SAPIEN/Genesis) but has no fault-injection or
fault-diagnosis baselines; its verifier methods correspond to our B4
stand-in. REFLECT (arXiv 2306.15724) is the closest LLM recovery
competitor and is the LLM-based future baseline, not a controlled
comparison. No prior system in the audit couples mechanism-level Bayesian
diagnosis with dependency invalidation and cost-optimal safe recovery
under a pre-registered protocol.

## 3. System

**Ledger and divergence (spec 6.2).** Every skill execution logs expected
effects (Gaussian pose predictions with tracked covariance, discrete
predicates) and observations; a Mahalanobis statistic with per-step
Bonferroni chi-square thresholds flags divergence, including scene-level
monitoring: objects the skill is not supposed to touch are expected at
their last verified pose, so a displaced bystander is itself a divergence.

**Diagnosis (spec 6.3).** An interpretable naive-Bayes posterior over seven
fault mechanisms (grasp miss, transport slip, pose error, displaced
collision, perception occlusion, controller abort, none) from six evidence
features extracted from telemetry; ambiguous posteriors (top-2 gap /
entropy gates) trigger information-gain-selected probes (alt-view
re-observation, gripper squeeze).

**Invalidation (spec 6.5).** The diagnosis maps to the task graph's
carried-variable edges; a conservative fixpoint marks downstream subgoals
stale so they re-verify preconditions instead of being blindly re-executed.

**Recovery (spec 6.4-6.6).** Recovery candidates are the affordances of the
plausible faults; the choice minimises expected time, extra actions and
failure probability, subject to a hard, independent safety veto; a loop
guard removes twice-failed same-type repairs; exhausted autonomy produces
a structured Section-9 escalation report.

**Safety (spec 6.8).** An independent supervisor checks every trajectory
against workspace, speed and inflated collision margins; vetoed motions
are never executed (0 unsafe executions across all 30,240 test episodes).

**Learning loop (Phase 4b).** Likelihoods, skill-conditioned priors and
affordance-failure tables are fitted from the episode database on a
never-scored calibration split; every sparse cell falls back to the
hand-set spec table and the fallback is recorded in the artifact.

**Deployment (ROS 2 Jazzy).** Six packages wrap the layer over 21 typed
interfaces; the same node code runs under a real rclpy bring-up, and under
an in-process message-bus shim in CI, with bag-replay determinism and
decision-path latency telemetry (p95 0.41 ms in-process).

## 4. Evaluation protocol (pre-registered)

Hypotheses H1-H5 per the pre-registration (docs/pre_registration.md):
recovery success over retry and full re-planning (H1); widening with
horizon (H2); fewer extra actions than full re-planning and no more than
retry (H3); unsafe rate no worse than baselines with zero unsafe
executions (H4); diagnosis value over plain verification (H5, ablation
A1).

Population: one injected fault per episode from seven mechanisms, six
horizons (1, 2, 3, 5, 7, 10), 30 episodes per cell, held-out seed range and
held-out task templates 15-19. Splits: calibration (never scored) fits
forward models and likelihood tables; validation tunes the divergence
alpha and escalation threshold; the test split was touched exactly once on
frozen code (tag v1.4.0-m6-freeze). Systems: B1-B5, RG, A1-A5, and the A6
perception-noise condition; identical skills, perception noise, planner
and seeds; only the divergence-handling logic differs. Statistics as
pre-registered: paired bootstrap (10k) CIs, McNemar exact, Wilcoxon
signed-rank, Holm-Bonferroni across the ablation family, OLS horizon
slopes with bootstrap CIs.

Two backends: the seeded kinematic world (CI regression path) and a MuJoCo
contact world behind the same interface, where the seven mechanisms are
re-located to contact level (misaligned grasp close, mid-transport grasp
release with physical fall and settle, impulse-level displacement,
perturbed release with settling, servo abort; occlusion and
stale-dependency remain mechanism-level by construction). Pinned fault
cells where a mechanism is structurally impossible for a template (e.g.
slip without a transport) are recorded as uninjectable and excluded with
effective N reported (2,016 effective fault episodes per system; 6,048
uninjectable episodes recorded).

## 5. Results

Pooled over both backends (per-backend tables in the archive; direction
identical on each):

| system | success | 95% CI | extra actions | FA rate | regret | unsafe |
|---|---|---|---|---|---|---|
| B1 no recovery | 0.096 | [.084, .109] | 1.85 | 0.160 | - | 0 |
| B2 fixed retry | 0.374 | [.354, .396] | 4.08 | 0.436 | - | 0 |
| B3 full replan | 0.473 | [.452, .495] | 9.73 | 0.240 | - | 0 |
| B4 verification | 0.380 | [.358, .401] | 8.42 | 0.288 | - | 0 |
| B5 fault pipeline | 0.615 | [.593, .636] | 5.43 | 0.077 | - | 0 |
| **RecoverGraph** | **0.788** | [.769, .805] | 5.39 | 0.070 | 1.56 | 0 |

**H1: confirmed.** RG - B2 +0.413 [0.388, 0.437]; RG - B3 +0.315
[0.289, 0.339]; RG - B5 +0.173 [0.156, 0.190]; McNemar p ~ 0 everywhere.

**H2: confirmed.** Success slopes per added subgoal: RG -0.002 vs B2
-0.042, B5 -0.057; bootstrap CIs of the slope differences exclude zero
(RG-B2 +0.039 [0.031, 0.048]; RG-B5 +0.054 [0.049, 0.060]).

**H3: partially failed, reported as measured.** RG uses fewer extra
actions than full re-planning (5.39 vs 9.73, Wilcoxon p ~ 0) but more
than fixed retry (5.39 vs 4.08). A post-hoc secondary analysis restricted
to episodes where both systems succeed (labelled post-hoc) shows the same
ordering (1.93 vs 0.84 for B2; 2.94 vs 4.48 for B3): causal recovery pays
an action premium for diagnosis and probes. The pre-registered criterion
"no higher than B2" is therefore failed; the go/no-go table records 4 of
5 criteria met.

**H4: confirmed.** Zero unsafe executions everywhere (hard requirement
met); vetoes route around margins; unsafe actions never executed.

**H5: confirmed.** Removing the causal diagnosis (A1) drops success by
0.168 (Holm-adjusted p ~ 0). Cost-based selection also matters (A4 drops
0.172). Two ablations produced null or inverted effects, reported as
measured: A2 (dependency invalidation removed) does not change success at
this N; A3 (uncertainty removed from detection: point estimates) is 0.049
*better* than RG - point estimates over-trigger (FA 0.194 vs 0.070) yet
recover more faults; we attribute this to the verified-state gate's
tolerance dominating the benefit of calibrated residual weighting in this
regime, and we do not claim the uncertainty model improves success.

**Diagnosis quality (test split).** RG: macro-F1 0.814, accuracy 0.864,
ECE 0.168; the calibrated A5 artifact: macro-F1 0.856, accuracy 0.907,
ECE 0.095, Brier 0.039. Confusion is concentrated where the mechanisms
genuinely overlap (displaced-collision vs slip) and is reported as
measured, per the pre-registration's honesty constraints.

**Per-fault (RG).** miss 0.949, slip 0.983, pose 0.932, perception 0.972,
controller 0.817, collision 0.402, stale-dependency 0.581. The two weak
cells are the honest frontier: collision recovery from a displaced
bystander needs scene-level repairs beyond the v1 macro set, and
stale-dependency repair is gripper-timing constrained.

**A6 (perception noise).** Under doubled perception noise RG falls to
0.393: the verified-state-update confidence gate is the noise-sensitive
component (camera-only verification with fixed tolerances). This bounds
transfer to noisy real perception and motivates adaptive tolerances.

**Physics-backend agreement.** The kinematic and MuJoCo backends
reproduce each other's ordering (G2 gate: fault-free success 0.98/0.99,
false-alarm 0.7%/1.0%, manifest-conditioned detection 0.92/0.98,
directional sign agreement 4/4, max per-system delta 0.075).

**ROS bring-up (M4).** 100 episodes end-to-end over the six packages'
real node code (topics/services/actions), reconciling with the in-process
harness (0.94 vs 0.97 success, zero unsafe both), bag replay determinism
6/6 identical posteriors, and safety vetoes on the live message flow.

**LADDER-aligned external validity.** On the LADDER-style task families
(bin/route/relay/revisit/ordered, 1-10 object-destination subgoals,
labelled inspired-by, not the pre-registered split) RG again leads every
baseline on both backends (kinematic 0.86 vs B5 0.593; MuJoCo 0.307 vs
0.153) with zero unsafe executions.

## 6. Discussion and limitations

The extra-action failure says something useful: causal recovery buys
success with actions, and if actions are expensive relative to episode
value, the blind retry policy is cheaper - the cost model's weights are a
deployment decision, not a constant of nature. The A3 inversion cautions
that uncertainty modelling must pay for itself against the tolerance
regime of the verification gate. Faults are injected by mechanism on two
simulation backends (self-cause realism on physics: falls, settles,
impulse displacements); hardware transfer remains future work (ADR-006),
and the human-study hypothesis H6 is deferred with its own
pre-registration. Primary cells are single-fault; compound-fault episodes
are reported separately as stress tests.

## 7. Reproducibility

Every episode is a deterministic function of (system, seed, task template,
condition); the archive records seeds, configs, the frozen tag and commit
hash; the 242-test suite gates every change; the pre-registration and its
go/no-go table were fixed before the test split existed as a runnable
configuration.

## Data availability

Repository: recovergraph (this repo); results archive:
experiments/results/formal (go_no_go.json, tables.md, archive.json,
figures); validation and calibration artifacts in configs/ and
experiments/results.
