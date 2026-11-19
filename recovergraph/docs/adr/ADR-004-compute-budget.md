# ADR-004: Compute budget

- **Status:** Proposed
- **Date:** 2026-11-18
- **Decides:** Open decision 4 (spec Section 16.4)

## Context
The Bayesian pipeline is CPU-only; a single GPU is needed only for the
learned-likelihood ablation (A5) and any learned perception.

## Decision
- Plan for **CPU-only** execution of the full test suite (1,260 episodes/system
  is ~35 min at the measured 4.8 s per 1,440 kinematic episodes; MuJoCo adds
  contact simulation cost, still CPU-parallel via Docker Compose).
- A5 runs only if a single GPU (>= 8 GB) is available; otherwise A5 is
  reported as not-run (the loader falls back to hand-set tables and marks the
  run skipped in results, as implemented in `systems.A5LearnedLikelihood`).

## Consequences
+ No GPU dependency for the primary go/no-go decision.
- A5 remains a planned upgrade, not a claim; pre-registration treats it as
  optional.
