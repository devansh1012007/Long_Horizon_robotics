# ADR-003: Human study in the first paper

- **Status:** Proposed
- **Date:** 2026-11-18
- **Decides:** Open decision 3 (spec Section 16.3)

## Context
Phase 7 (HRI study) is optional; the spec flags it as a scope-creep risk
(Section 15). The core claims (H1-H5) need only Phases 0-6.

## Decision
**Defer the human study** to a second paper or a major-revision extension.
The first submission reports the escalation *mechanism* (Section 9 evidence
package + interface-ready JSON) without the H6 experiment. The dashboard
(FastAPI/React) is likewise deferred; the JSON schema is already fixed so the
study can be run without re-engineering.

## Consequences
+ Timeline shrinks by ~3 weeks; no ethics approval needed for the first paper.
- H6 stays untested; the paper must not claim operator-benefit results.
