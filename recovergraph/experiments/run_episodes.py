"""Batch episode runner (spec 10.2-10.3): runs systems x conditions x
horizons x seeds with shared seeds for pairing, writes SQLite + CSV.

Usage:
  python -m experiments.run_episodes --systems RG B2 B3 --horizons 1 3 5 \
      --conditions single none compound --episodes 15 --out experiments/results/smoke
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time

from rg_core.episode_db import EpisodeDB
from rg_core.taskgen import make_task
from rg_core.types import SkillResult
from rgsim.noise import NoiseCfg
from rgsim.pipeline import EpisodeRunner
from systems import make_system

CONDITIONS = ("none", "single", "compound")

# A6 is the pre-registered perception-noise condition (spec 10.4): the RG
# system under a doubled perception-noise model (runner-level condition,
# identical skills/planner/seeds).
A6_NOISE = NoiseCfg(pose_sigma=0.016, yaw_sigma=0.10, dropout=0.04,
                    ee_sigma=0.008, occl_cov_mult=8.0)


def run_cell(system_id: str, horizon: int, condition: str, seeds: list,
             out_dir: str, db: EpisodeDB | None, backend: str = "kinematic",
             fault: str | None = None, artifact_path: str | None = None,
             alpha_step: float | None = None,
             delta: float | None = None):
    rows = []
    noise_cfg = A6_NOISE if system_id == "A6" else None
    sys_id = "RG" if system_id == "A6" else system_id
    for seed in seeds:
        template_id = seed % 20            # 20 templates per horizon (spec 8)
        task = make_task(horizon, template_id, seed)
        system = make_system(sys_id, artifact_path)
        if delta is not None and hasattr(system, "recovery_weights"):
            system.recovery_weights = {"delta": delta}
        if system_id in ("A5", "RG-LEARNED") and not getattr(
                system, "learned_available", True):
            rows.append({"system": system_id, "condition": condition,
                         "horizon": horizon, "template": template_id,
                         "seed": seed, "skipped": "no learned-likelihood artifact"})
            continue
        ep_id = f"ep_{system_id}_{condition}_h{horizon}_{seed}"
        pin = [fault] if fault else None
        if fault:
            ep_id += f"_{fault}"
        runner = EpisodeRunner(system, task, seed=seed, condition=condition,
                               db=db, ep_id=ep_id, backend=backend,
                               faults=pin, noise_cfg=noise_cfg)
        if alpha_step is not None:
            runner.alpha_step_override = alpha_step
        res = runner.run()
        m = res["metrics"]
        labels = [e["label"] for e in res["faults"]]
        rows.append({
            "system": system_id, "condition": condition, "horizon": horizon,
            "template": template_id, "seed": seed, "outcome": res["outcome"],
            "success": int(m["success"]), "extra_actions": m["extra_actions"],
            "recovery_time_s": round(m["recovery_time_s"], 3),
            "vetoes": m["vetoes"], "unsafe_executions": m["unsafe_executions"],
            "interventions": m["interventions"], "escalations": m["escalations"],
            "flags": m["flags"], "flags_fault_free": m["flags_fault_free"],
            "steps": m["steps"],
            "diag_correct": ("" if m["diag_correct"] is None
                             else int(m["diag_correct"])),
            "regret": round(m["regret"], 3),
            "faults": (fault if (fault and labels) else
                       ("|".join(labels) if labels else "none")),
            "task_id": res["task_id"],
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="RecoverGraph batch runner")
    ap.add_argument("--systems", nargs="+",
                    default=["B1", "B2", "B3", "B4", "B5", "RG"])
    ap.add_argument("--horizons", nargs="+", type=int, default=[1, 2, 3, 5])
    ap.add_argument("--conditions", nargs="+", default=["single", "none"])
    ap.add_argument("--episodes", type=int, default=15,
                    help="episodes (seeds) per cell; seed = base_seed + i")
    ap.add_argument("--base-seed", type=int, default=1000)
    ap.add_argument("--out", default="experiments/results/smoke")
    ap.add_argument("--backend", default="kinematic",
                    choices=["kinematic", "mujoco"])
    ap.add_argument("--faults", nargs="*", default=None,
                    help="pin fault types for single-fault cells (one cell "
                         "per fault; pre-registered cell structure)")
    ap.add_argument("--artifact", default=None,
                    help="learned-likelihood artifact path (A5 / RG-LEARNED)")
    ap.add_argument("--alpha-step", type=float, default=None,
                    help="override the divergence alpha (validation tuning)")
    ap.add_argument("--templates", nargs="*", type=int, default=None,
                    help="restrict seeds to these template ids (mod 20); the "
                         "pre-registered test split uses 15-19")
    ap.add_argument("--delta", type=float, default=None,
                    help="override the escalation weight delta (validation "
                         "tuning)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    db = EpisodeDB(os.path.join(args.out, "episodes.db"))
    all_rows: list = []
    t0 = time.time()
    fault_cells = args.faults or [None]
    for horizon in args.horizons:
        for condition in args.conditions:
            assert condition in CONDITIONS, condition
            if args.templates:
                wanted = set(args.templates)
                span = args.episodes * (20 // max(1, len(wanted)) + 1) * 2
                seeds = [s for s in range(args.base_seed,
                                          args.base_seed + span)
                         if s % 20 in wanted][:args.episodes]
                assert len(seeds) == args.episodes, \
                    f"not enough seeds for templates {wanted}"
            else:
                seeds = list(range(args.base_seed,
                                   args.base_seed + args.episodes))
            for fault in fault_cells:
                if fault is not None and condition != "single":
                    continue
                for system_id in args.systems:
                    rows = run_cell(system_id, horizon, condition, seeds,
                                    args.out, db, backend=args.backend,
                                    fault=fault,
                                    artifact_path=args.artifact,
                                    alpha_step=args.alpha_step,
                                    delta=args.delta)
                    all_rows += rows
                    ok = sum(r.get("success", 0) for r in rows)
                    ftag = fault or "any"
                    print(f"h={horizon} {condition:8s} {ftag:16s} "
                          f"{system_id:4s} [{args.backend:9s}]: "
                          f"{ok}/{len(rows)} success")
    db.close()

    # CSV (skip dict-skipped rows for the flat table but keep a record)
    csv_path = os.path.join(args.out, "results.csv")
    if all_rows:
        fieldnames = sorted({k for r in all_rows for k in r})
        with open(csv_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(all_rows)

    meta = {"systems": args.systems, "horizons": args.horizons,
            "conditions": args.conditions, "episodes_per_cell": args.episodes,
            "base_seed": args.base_seed, "backend": args.backend,
            "pinned_faults": args.faults,
            "wall_time_s": round(time.time() - t0, 1),
            "git_commit": _git_head()}
    with open(os.path.join(args.out, "run_meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"done in {meta['wall_time_s']}s -> {csv_path}")


def _git_head() -> str:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
                                        text=True).strip()[:12]
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()
