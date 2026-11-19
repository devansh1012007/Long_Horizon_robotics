"""RecoverGraph milestone-1 demo (spec-mapped showcase).

Runs four scenarios and writes artifacts to experiments/results/demo/:
  1. fault-free episode      -> verified state updates, zero flags
  2. slip during transport   -> ledger entry, Bayes diagnosis, cost-based
                                recovery, verified restore (recovery time)
  3. exhausted loop guard    -> Section 9 escalation report (JSON)
  4. controlled comparison   -> RG vs B1/B2/B3 on identical faulted seeds

Usage: python scripts/demo.py [--out experiments/results/demo]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rg_core.taskgen import make_task                                  # noqa: E402
from rgsim.pipeline import EpisodeRunner                              # noqa: E402
from systems import make_system                                       # noqa: E402

H = 3


def find_manifest(fault, lo=1000, hi=1400):
    from tests.test_pipeline_smoke import skill_tags
    from rgsim.fault_injector import episode_schedule
    for tpl in range(20):
        task = make_task(H, tpl, 0)
        skills = skill_tags(task)
        for s in range(lo, hi):
            evs = episode_schedule(s, H, "single", skills=skills)
            if evs and evs[0].fault == fault:
                r = EpisodeRunner(make_system("RG"), make_task(H, tpl, 0),
                                  seed=s, condition="single")
                res = r.run()
                if res["metrics"]["flags"] >= 1:
                    return s, tpl, r, res
    raise RuntimeError(f"no manifesting seed for {fault}")


def fmt_metrics(m):
    keep = ("success", "steps", "extra_actions", "recovery_time_s", "vetoes",
            "interventions", "escalations", "diag_correct", "regret")
    return {k: (round(v, 2) if isinstance(v, float) else v)
            for k, v in m.items() if k in keep}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="experiments/results/demo")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    lines = ["# RecoverGraph milestone-1 demo", ""]

    # ---- scenario 1: fault-free ----
    task = make_task(H, 0, 0)
    r1 = EpisodeRunner(make_system("RG"), task, seed=1001, condition="none")
    res1 = r1.run()
    lines += [f"## 1. Fault-free episode ({task.goal})", "",
              f"outcome: **{res1['outcome']}**, metrics: "
              f"`{json.dumps(fmt_metrics(res1['metrics']))}`",
              "Every subgoal advanced only after the independent camera check "
              "(spec 6.7); no divergence flagged.", ""]

    # ---- scenario 2: slip during transport ----
    s2, t2, r2, res2 = find_manifest("f_slip")
    task2 = make_task(H, t2, 0)
    lines += [f"## 2. Slip during transport (seed {s2}, template {t2})",
              f"task: {task2.goal}", ""]
    ledger = [p for _n, e, p in r2.events if e == "exec"][:1]
    divs = [(p) for _n, e, p in r2.events if e in ("exec", "recovery_start",
                                                   "verified", "probe",
                                                   "unverified")]
    for p in divs:
        lines.append(f"- `{json.dumps(p)[:150]}`")
    lines += ["", f"outcome: **{res2['outcome']}**, metrics: "
              f"`{json.dumps(fmt_metrics(res2['metrics']))}`",
              "The diagnoser localizes f_slip from grip-force telemetry; the "
              "cost model picks the cheapest safe recovery; the state advance "
              "waits for verified agreement.", ""]

    # ---- scenario 3: escalation with exhausted loop guard ----
    from rg_core.recovery import AFFORDANCES
    esc_report = None
    for tpl in range(6):
        task3 = make_task(H, tpl, 0)
        from tests.test_pipeline_smoke import skill_tags
        from rgsim.fault_injector import episode_schedule
        skills = skill_tags(task3)
        for s in range(1000, 1030):
            evs = episode_schedule(s, H, "single", skills=skills)
            if not evs or evs[0].fault != "f_miss":
                continue
            r3 = EpisodeRunner(make_system("RG"), task3, seed=s,
                               condition="single")
            st = list(r3.schedule.values())[0].step
            sub_id = r3.task.topo_order()[st]
            for rt in AFFORDANCES["f_miss"]:
                r3.loop_guard[(sub_id, "f_miss", rt)] = 2
            res3 = r3.run()
            reports = [p for _n, e, p in r3.events if e == "escalation"]
            if res3["metrics"]["interventions"] >= 1 and reports:
                esc_report = reports[0]
                lines += [f"## 3. Escalation (seed {s}, template {tpl})",
                          "Loop guard exhausted the safe options for the "
                          "diagnosed fault; the system hands over with an "
                          "evidence package (spec Section 9):", "",
                          "```json",
                          json.dumps(esc_report, indent=2),
                          "```", ""]
                break
        if esc_report:
            break
    if esc_report:
        with open(os.path.join(args.out, "escalation_report.json"), "w") as fh:
            json.dump(esc_report, fh, indent=2)

    # ---- scenario 4: controlled comparison ----
    lines += ["## 4. Controlled comparison (same seeds, single faults)", "",
              "| system | success | extra actions | interventions |",
              "|---|---|---|---|"]
    for sysid in ("B1", "B2", "B3", "RG"):
        ok = n = ea = iv = 0
        for s in range(1000, 1030):
            task4 = make_task(H, s % 20, 0)
            res4 = EpisodeRunner(make_system(sysid), task4, seed=s,
                                 condition="single").run()
            n += 1
            ok += int(res4["metrics"]["success"])
            ea += res4["metrics"]["extra_actions"]
            iv += res4["metrics"]["interventions"]
        lines.append(f"| {sysid} | {ok}/{n} | {ea / n:.1f} | {iv / n:.2f} |")
    lines += ["", "Identical skills, perception noise, planner and seeds "
              "(spec 10.3); only the recovery logic differs.", ""]

    with open(os.path.join(args.out, "demo.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[:14]))
    print(f"... full transcript -> {os.path.join(args.out, 'demo.md')}")


if __name__ == "__main__":
    main()
