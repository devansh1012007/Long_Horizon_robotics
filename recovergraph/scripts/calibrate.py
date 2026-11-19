"""M5 calibration: fit the learned-likelihood artifact on the calibration
split (never scored) + sensitivity analysis vs the hand-set tables.

Runs the calibration split (RG, kinematic + MuJoCo backends), fits the
likelihood / prior / affordance-failure tables from the episode DBs, and
writes configs/learned_artifact_v1.json. Hand-set cells are kept (honest
fallback) wherever the calibration counts fall below the minimum.

Run:  python -m scripts.calibrate [--quick]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CAL_SEEDS = 50
FAULTS7 = ["f_miss", "f_slip", "f_pose", "f_collision", "f_perception",
           "f_controller", "stale_dependency"]


def run_split(backend: str, out: str, episodes: int) -> str:
    cmd = [sys.executable, "-m", "experiments.run_episodes",
           "--systems", "RG", "--horizons", "1", "2", "3", "5",
           "--conditions", "single", "none", "--episodes", str(episodes),
           "--base-seed", "2000", "--backend", backend, "--out", out]
    print(">>", " ".join(cmd))
    subprocess.run(cmd, cwd=REPO, check=True)
    return os.path.join(out, "episodes.db")


def run_pinned(backend: str, out: str, episodes: int) -> str:
    """Pinned fault cells: one cell per fault type, so every mechanism
    contributes evidence rows to the likelihood fit."""
    cmd = [sys.executable, "-m", "experiments.run_episodes",
           "--systems", "RG", "--horizons", "1", "2", "3", "5",
           "--conditions", "single", "--episodes", str(episodes),
           "--base-seed", "2200", "--backend", backend, "--out", out,
           "--faults"] + FAULTS7
    print(">>", " ".join(cmd))
    subprocess.run(cmd, cwd=REPO, check=True)
    return os.path.join(out, "episodes.db")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--reuse", action="store_true",
                    help="reuse existing calibration DBs instead of rerunning")
    args = ap.parse_args()
    eps = 6 if args.quick else CAL_SEEDS

    if args.reuse:
        dbs = ["experiments/results/calibration_kin/episodes.db",
               "experiments/results/calibration_mj/episodes.db",
               "experiments/results/calibration_pin_kin/episodes.db",
               "experiments/results/calibration_pin_mj/episodes.db"]
        prior_dbs = ["experiments/results/calibration_kin/episodes.db",
                     "experiments/results/calibration_mj/episodes.db"]
    else:
        dbs = [run_split("kinematic", "experiments/results/calibration_kin", eps)]
        dbs.append(run_split("mujoco", "experiments/results/calibration_mj", eps))
        dbs.append(run_pinned("kinematic", "experiments/results/calibration_pin_kin", eps))
        dbs.append(run_pinned("mujoco", "experiments/results/calibration_pin_mj", eps))

    if not args.reuse:
        prior_dbs = dbs[:2]          # natural-rate DBs (random fault cells)

    from rg_core.learning import build_artifact, save_artifact
    artifact = build_artifact(dbs, min_count=15, prior_dbs=prior_dbs)
    path = save_artifact(artifact, os.path.join(REPO, "configs",
                                                "learned_artifact_v1.json"))
    summary = {
        "n_rows": artifact["n_rows"],
        "sensitivity_vs_handset": artifact["sensitivity_vs_handset"],
        "likelihood_fit_meta": artifact["fit_meta"]["likelihoods"],
        "pfail_fit_meta": {k: artifact["fit_meta"]["pfail"][k]
                           for k in ("min_count", "recoveries_with_sparse_cells")},
        "forward_params": artifact["forward_params"],
    }
    with open(os.path.join(REPO, "experiments", "results",
                           "calibration_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2))
    print(f"artifact -> {path}")


if __name__ == "__main__":
    main()
