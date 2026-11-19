# ADR-005: Target venue and timeline

- **Status:** Proposed (owner to fix the deadline)
- **Date:** 2026-11-18
- **Decides:** Open decision 5 (spec Section 16.5)

## Context
The venue deadline fixes the working timeline. Candidate path per spec:
workshop paper first, full paper after results.

## Decision
- **First output:** a workshop paper (robotics conference workshop on failure
  handling / execution monitoring) with Phases 0-6 results, submitted after
  the go/no-go evaluation.
- **Full paper:** after the Phase 0-6 gate passes and the related-work matrix
  confirms the wedge; include the human study only if ADR-003 is revisited.
- Working timeline: milestone 1 (this sprint) covers spec steps 1, 3, 4, 5, 6
  of Section 17; Phases 0 (LADDER integration) and 1 (MoveIt/MuJoCo) follow.

## Consequences
+ Workshop route gives an early, honest artefact with the reproducibility
  package (this repo) as the main contribution if results are mixed.
- The venue choice must be re-confirmed once LADDER integration effort is
  known (Phase 0 gate).
