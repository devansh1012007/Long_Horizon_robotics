# ADR-006: Real-hardware stretch demo

- **Status:** Proposed - deferred
- **Date:** 2026-11-18
- **Decides:** Open decision 6 (spec Section 16.6)

## Context
Hardware is out of scope for v1 (spec Section 1) with a stretch goal after
Phase 6. Simulation-to-reality transfer is a listed risk (Section 15).

## Decision
**Do not attempt hardware in the first paper.** If the project continues past
the gate, the minimum viable hardware demo is: Franka Panda, AprilTag
calibration, the 7-fault suite subset that is safely injectable by mechanism
(occlusion via lighting/mask, controller abort via software stop, slip via
reduced grip force), running the same rg_core pipeline against live
estimators. A hardware demo must reuse the identical ledger/diagnosis/
recovery code path (rg_core) with no algorithm changes.

## Consequences
+ Milestone scope stays closed; the ROS 2 node structure is already the
  hardware integration surface.
- Any transfer claim in the paper is limited to the A6 noise ablation.
