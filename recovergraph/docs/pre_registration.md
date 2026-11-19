# Pre-Registration: RecoverGraph research experiments

**Pre-registered:** 2026-11-18 (before any held-out test-split run; the commit
hash of this file is recorded in the experiment tracking table at test time).
**Status:** hypotheses, metrics, splits and statistics plan fixed per spec
Sections 2, 3, 10.2, 10.5, 10.6.

## 1. Hypotheses (spec Section 2, verbatim)

- **H1.** Causal recovery achieves higher recovery success than fixed-retry and full-replan baselines under injected faults.
- **H2.** The advantage grows with task horizon (more subgoals).
- **H3.** Causal recovery uses fewer extra actions than baselines for equal success.
- **H4.** Unsafe-action rate is less than or equal to baselines.
- **H5.** Causal diagnosis adds value beyond plain state verification (ablation A1).
- **H6.** Evidence-grounded explanations improve operator intervention quality (human study, Phase 7; pre-registration for the study protocol is added before recruitment).

## 2. Primary metrics (spec 10.5)

1. End-to-end success: fraction of episodes completing the goal within the step/time budget.
2. Recovery time: seconds from divergence detection to restored verified state.
3. Extra actions: total actions minus nominal plan length.
4. Unsafe-action rate: safety-supervisor vetoes per 100 actions; unsafe executions (target: exactly 0).
5. Intervention count: human escalations per episode.
6. Diagnosis accuracy: argmax posterior equals injected label; per-class confusion matrix; macro-F1.
7. Recovery regret: cost of chosen recovery minus oracle-best feasible cost (oracle knows the true fault).
8. Calibration: expected calibration error and Brier score of posteriors; reliability diagram.
9. False-alarm rate: divergence flagged on fault-free steps.

Fault episodes with the `stale_dependency` label are excluded from diagnosis-accuracy scoring (the Bayes diagnoser's hypothesis space does not contain a dependency-level fault; the mechanism is handled by invalidation). Compound episodes are reported separately as a stress test and excluded from the primary single-fault comparisons.

## 3. Population, splits and seeds (spec 10.2)

- Test population: single injected fault per episode; faults drawn from {f_miss, f_slip, f_pose, f_collision(displaced), f_perception, f_controller, stale_dependency}; 7 fault types x 6 horizons (1, 2, 3, 5, 7, 10) x 30 episodes = 1,260 test episodes per system.
- Splits:
  - **Calibration:** fault-free + single-fault episodes used to fit forward-model parameters and likelihood tables (never scored).
  - **Validation:** episodes used to tune divergence alpha per group, cost weights, and escalation thresholds (never scored).
  - **Test:** held-out seed range and held-out task templates (templates 15-19 excluded from calibration/validation); touched exactly once, after all tuning freezes.
- Systems compared: B1 (no recovery), B2 (fixed retry <= 3), B3 (full replanning), B4 (verification-based orchestration), B5 (fault pipeline: threshold detection + forward/backward recovery), RG (RecoverGraph full). Ablations A1-A4 (+A5 when a learned-likelihood artifact exists, +A6 as a noise condition).
- Controlled variables across systems: identical skills, identical perception noise model, identical planner, identical seeds (spec 10.3).

## 4. Statistics (spec 10.6)

- Primary comparison: RG vs each baseline on end-to-end success, paired by (template, seed); paired bootstrap with 10,000 resamples for the 95% CI of the paired difference; McNemar exact test for binary success; Wilcoxon signed-rank for non-binary metrics (extra actions, recovery time).
- Horizon effect (H2): OLS slope of success vs horizon per system; slope difference RG - baseline with a bootstrap CI.
- Ablations (H5): McNemar p-values for RG vs A1-A4, Holm-Bonferroni-adjusted across the ablation family.
- Results reported per horizon, never only pooled; pooled numbers appear only as summaries.
- Confounder control: recovery regret and false-alarm rate are reported for every system to show that success gains are not bought with over-triggering recovery.

## 5. Go / no-go criteria (spec Section 3, on the held-out test split)

1. Recovery success of RG exceeds both Retry (B2) and Full-Replan (B3), with the paired bootstrap 95% CI of the difference excluding 0.
2. The gap widens at longer horizons (slope difference CI excludes 0).
3. Mean extra actions lower than B3 and no higher than B2.
4. Unsafe-action rate <= every baseline (hard requirement: zero unsafe executions).
5. Ablation A1 shows a statistically meaningful drop (Holm-adjusted p < 0.05).

If the criteria fail because gains vanish with the planner/perception held fixed, the planned fallback is a negative-result/analysis paper (spec Section 15) - declared here in advance.

## 6. Honesty constraints

- Fault labels are defined by injected mechanism; the confusion matrix is reported as measured, including ambiguous cases.
- Milestone-1 results (smoke runs, reduced N) are labelled as such wherever shown and are never presented as the test result.
- No novelty claim is made until the literature audit (docs/literature_audit.md) is complete.
