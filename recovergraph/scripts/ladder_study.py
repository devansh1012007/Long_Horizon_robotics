"""M7 external-validity study: LADDER-aligned task distribution.

Runs the systems on the LADDER-style task families (docs/LADDER_ADAPTER.md
structural adoption; labelled as such, NOT the pre-registered test split):
RG vs B2/B3/B5 across LADDER subgoal counts 1-10, both backends,
single-fault episodes (random faults, compatible injection).

Run: python -m scripts.ladder_study [--quick]
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

from rg_core.taskgen_ladder import FAMILIES, make_ladder_task
from rg_core.episode_db import EpisodeDB
from rgsim.pipeline import EpisodeRunner
from systems import make_system


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    systems = ["B2", "B3", "B5", "RG"]
    seeds = list(range(4 if args.quick else 10))
    out = "experiments/results/ladder_study"
    os.makedirs(out, exist_ok=True)
    db = EpisodeDB(os.path.join(out, "episodes.db"))
    rows = []
    for backend in ("kinematic", "mujoco"):
        for family in FAMILIES:
            for n_sub in (2, 5, 10):
                for seed in seeds:
                    task = make_ladder_task(family, n_sub, seed)
                    for sysid in systems:
                        r = EpisodeRunner(make_system(sysid), task,
                                           seed=seed + 9000,
                                           condition="single", db=db,
                                           backend=backend).run()
                        m = r["metrics"]
                        rows.append({
                            "family": family, "n_subgoals": n_sub,
                            "ladder_subgoals": n_sub,
                            "skill_horizon": task.horizon(),
                            "seed": seed, "system": sysid,
                            "backend": backend,
                            "success": int(m["success"]),
                            "extra_actions": m["extra_actions"],
                            "unsafe": m["unsafe_executions"]})
    db.close()
    by = {}
    for r in rows:
        by.setdefault((r["system"], r["backend"]), []).append(r)
    table = {f"{s}/{b}": round(float(np.mean([x["success"] for x in v])), 3)
             for (s, b), v in by.items()}
    horiz = {}
    for r in rows:
        if r["system"] == "RG" and r["backend"] == "mujoco":
            horiz.setdefault(r["n_subgoals"], []).append(r["success"])
    slope = float(np.polyfit(list(horiz), [np.mean(horiz[h])
                                            for h in sorted(horiz)], 1)[0])
    report = {
        "n_episodes": len(rows), "systems": systems,
        "families": FAMILIES, "subgoal_counts": [2, 5, 10],
        "success_by_system_backend": table,
        "rg_mujoco_success_by_ladder_subgoals": {
            str(h): round(float(np.mean(v)), 3)
            for h, v in sorted(horiz.items())},
        "rg_horizon_slope": round(slope, 5),
        "unsafe_total": sum(r["unsafe"] for r in rows),
        "note": "LADDER-aligned distribution (inspired-by, per "
                "docs/LADDER_ADAPTER.md); not the pre-registered test split.",
    }
    json.dump(report, open(os.path.join(out, "report.json"), "w"),
              indent=2, default=float)
    json.dump(rows, open(os.path.join(out, "rows.json"), "w"), default=float)
    print(json.dumps(report, indent=2, default=float))


if __name__ == "__main__":
    main()
