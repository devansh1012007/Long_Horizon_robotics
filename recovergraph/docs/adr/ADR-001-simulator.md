# ADR-001: Simulation environment

- **Status:** Proposed (pending Phase 0 gate confirmation)
- **Date:** 2026-11-18
- **Decides:** Open decision 1 (spec Section 16.1)

## Context
The spec recommends MuJoCo as the primary simulator with Gazebo Harmonic +
MoveIt 2 as a secondary check, with the final choice gated on what LADDER
requires (Phase 0). The milestone-1 implementation ran in this sandbox where
no ROS or MuJoCo runtime is available.

## Decision
1. **Primary (post-Phase-0):** MuJoCo, per the spec recommendation, with a
   ROS 2 bridge (`rg_sim` package).
2. **Milestone 1 (this repo, implemented):** a pure-Python *kinematic*
   tabletop simulator (`rgsim`) with probabilistic skill outcomes and
   mechanism-based fault injection. It runs in milliseconds per episode,
   is fully seeded, and validates the ledger/diagnosis/recovery algorithms
   without contact physics.
3. The `rg_sim` ROS package currently wraps the kinematic world behind the
   same service/topic interface the MuJoCo bridge will expose, so the swap is
   localised.

## Consequences
+ Algorithm development and the full experiment pipeline are unblocked and
  reproducible (1440-episode smoke batch in ~5 s, CPU only).
- Contact-level faults (real unmodeled pushes) are approximated by
  mechanism-level injection; transfer risk is acknowledged in spec Section 15
  and mitigated by the A6 perception-realism ablation and the Phase 0 gate.
