# ADR-002: Robot model and gripper

- **Status:** Proposed
- **Date:** 2026-11-18
- **Decides:** Open decision 2 (spec Section 16.2)

## Context
Spec: Franka Panda (or whichever arm LADDER uses) with a parallel gripper.
Kinematic milestone does not need a specific arm.

## Decision
- Target **Franka Panda + parallel-jaw gripper** for MuJoCo and hardware.
- The kinematic sim models an EE point with a parallel gripper abstraction
  (width, force, discrete grasp state) whose interface matches the Panda
  hand: the belief state and ledger fields are gripper-model agnostic.
- If LADDER standardises on a different arm, adopt it (Phase 0 gate) - the
  swap affects `rg_sim` scene setup only; rg_core is arm-agnostic.

## Consequences
+ No algorithmic coupling to the arm.
- Joint-space telemetry (q_t) is abstracted to EE pose + gripper in
  milestone 1; the belief-state schema already reserves q_t fields.
