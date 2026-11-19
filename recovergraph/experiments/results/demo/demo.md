# RecoverGraph milestone-1 demo

## 1. Fault-free episode (stack red cube on blue cube, then pick up yellow cube)

outcome: **success**, metrics: `{"success": true, "steps": 3, "extra_actions": 0, "recovery_time_s": 0.0, "vetoes": 0, "interventions": 0, "diag_correct": null, "regret": 0.0, "escalations": 0}`
Every subgoal advanced only after the independent camera check (spec 6.7); no divergence flagged.

## 2. Slip during transport (seed 1005, template 2)
task: put green cube in the bin

- `{"subgoal": "g1", "status": "ok", "events": ["grasp_closed"]}`
- `{"subgoal": "g1", "conf": 1.0}`
- `{"subgoal": "g2", "status": "ok", "events": ["friction_mult=0.579168612716581", "slip_drop"]}`
- `{"type": "retry_modified", "subgoal": "g2"}`
- `{"subgoal": "g2", "status": "ok", "events": []}`
- `{"subgoal": "g2", "conf": 0.988}`
- `{"subgoal": "g3", "status": "ok", "events": ["released"]}`
- `{"subgoal": "g3", "conf": 0.996}`

outcome: **success**, metrics: `{"success": true, "steps": 4, "extra_actions": 3, "recovery_time_s": 10.0, "vetoes": 0, "interventions": 0, "diag_correct": true, "regret": 0.56, "escalations": 0}`
The diagnoser localizes f_slip from grip-force telemetry; the cost model picks the cheapest safe recovery; the state advance waits for verified agreement.

## 3. Escalation (seed 1004, template 0)
Loop guard exhausted the safe options for the diagnosed fault; the system hands over with an evidence package (spec Section 9):

```json
{
  "failure_reason": "Grasp missed the object (p=0.91)",
  "evidence": [
    "gripper closed to near-zero width"
  ],
  "alternatives": [],
  "stale_subgoals": [],
  "why_escalating": "all safe options exceed autonomy risk threshold"
}
```

## 4. Controlled comparison (same seeds, single faults)

| system | success | extra actions | interventions |
|---|---|---|---|
| B1 | 9/30 | 0.6 | 0.00 |
| B2 | 18/30 | 0.8 | 0.00 |
| B3 | 18/30 | 4.8 | 0.00 |
| RG | 29/30 | 2.1 | 0.00 |

Identical skills, perception noise, planner and seeds (spec 10.3); only the recovery logic differs.

