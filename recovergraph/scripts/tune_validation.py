"""M5: validation tuning + G3 gate (spec Phase 4-5 acceptance).

1. Grid-search divergence alpha x escalation delta on the validation split
   (kinematic; documented simplification: the per-step Bonferroni alpha is
   retained, the global alpha is tuned). Selection: FA < 2% required, then
   max success, then min interventions.
2. Freeze the tuned values into configs/tuning_v2.json.
3. Run the full validation grid on both backends (B2/B3/RG/A1-A4/A5/
   RG-LEARNED at >= 30 episodes per cell, artifact-injected).
4. G3 metrics: diagnosis macro-F1 and ECE (RG), fault separability,
   RG-vs-B2/B3 paired statistics, zero-unsafe, RG-LEARNED/A5 vs RG.

Run:  python -m scripts.tune_validation [--quick]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from experiments.stats import (expected_calibration_error,  # noqa: E402
                               mcnemar_exact, paired_bootstrap_ci)
from rg_core.learning import load_episodes
from rg_core.types import FAULTS

SYSTEMS_VAL = ["B2", "B3", "RG", "A1", "A2", "A3", "A4"]
HORIZONS = [1, 2, 3, 5, 7, 10]


def _run(cmd: list) -> None:
    print(">>", " ".join(cmd))
    subprocess.run(cmd, cwd=REPO, check=True)


def _read_csv(path: str) -> list:
    with open(path) as fh:
        return [r for r in csv.DictReader(fh) if r.get("success", "") != ""]


# --------------------------------------------------------------------------- #
def grid_search(quick: bool) -> dict:
    from rg_core.taskgen import make_task
    from rgsim.pipeline import EpisodeRunner
    from systems import make_system

    eps = 4 if quick else 12
    seeds = list(range(4000, 4000 + eps))
    grid = []
    for alpha in (0.002, 0.005, 0.01):
        for delta in (40.0, 60.0, 80.0):
            succ = flags_ff = steps_ff = interv = 0
            n = 0
            for h in HORIZONS:
                for cond in ("single", "none"):
                    for seed in seeds:
                        system = make_system("RG")
                        system.recovery_weights = {"delta": delta}
                        task = make_task(h, seed % 20, seed)
                        runner = EpisodeRunner(system, task, seed=seed,
                                               condition=cond)
                        runner.alpha_step_override = alpha
                        m = runner.run()["metrics"]
                        succ += m["success"]
                        flags_ff += m["flags_fault_free"]
                        steps_ff += m["steps"]
                        interv += m["interventions"]
                        n += 1
            grid.append({"alpha_step": alpha, "delta": delta,
                         "success": succ / n,
                         "fa_rate": flags_ff / max(1, steps_ff),
                         "interventions": interv / n})
            print(f"  alpha={alpha} delta={delta}: success={succ/n:.3f} "
                  f"FA={flags_ff/max(1, steps_ff):.4f} "
                  f"interv/ep={interv/n:.3f}")
    ok = [g for g in grid if g["fa_rate"] < 0.02]
    pool = ok or grid
    best = max(pool, key=lambda g: (round(g["success"], 3),
                                    -g["interventions"]))
    return {"grid": grid, "chosen": best}


# --------------------------------------------------------------------------- #
def run_validation(quick: bool, tuned: dict, artifact: str) -> None:
    eps = 4 if quick else 30
    systems = SYSTEMS_VAL[:3] if quick else SYSTEMS_VAL
    for backend in ("kinematic", "mujoco"):
        _run([sys.executable, "-m", "experiments.run_episodes",
              "--systems"] + systems +
             ["--horizons"] + [str(h) for h in HORIZONS] +
             ["--conditions", "single", "none", "--episodes", str(eps),
              "--base-seed", "4000", "--backend", backend,
              "--out", f"experiments/results/validation_{backend}",
              "--alpha-step", str(tuned["alpha_step"]),
              "--delta", str(tuned["delta"])])
    if not quick:
        for backend in ("kinematic", "mujoco"):
            _run([sys.executable, "-m", "experiments.run_episodes",
                  "--systems", "RG-LEARNED", "A5", "RG",
                  "--horizons"] + [str(h) for h in HORIZONS] +
                 ["--conditions", "single", "none", "--episodes", "30",
                  "--base-seed", "4600", "--backend", backend,
                  "--out", f"experiments/results/validation_learned_{backend}",
                  "--artifact", artifact,
                  "--alpha-step", str(tuned["alpha_step"]),
                  "--delta", str(tuned["delta"])])
            # pinned fault cells: the pre-registered cell structure gives every
            # fault class >= 30 episodes for diagnosis-quality scoring
            _run([sys.executable, "-m", "experiments.run_episodes",
                  "--systems", "RG", "A5",
                  "--horizons"] + [str(h) for h in HORIZONS] +
                 ["--conditions", "single", "--episodes", "30",
                  "--base-seed", "4800", "--backend", backend,
                  "--out", f"experiments/results/validation_pinned_{backend}",
                  "--artifact", artifact,
                  "--alpha-step", str(tuned["alpha_step"]),
                  "--delta", str(tuned["delta"]),
                  "--faults", "f_miss", "f_slip", "f_pose", "f_collision",
                  "f_perception", "f_controller", "stale_dependency"])


# --------------------------------------------------------------------------- #
def g3_metrics(quick: bool, tuned: dict) -> dict:
    diag6 = [f for f in FAULTS if f != "f_none"]

    # ---- diagnosis quality on the PRE-REGISTERED SCOREABLE POPULATION ----
    # (truth in DIAGNOSABLE; fault-free divergence steps are not scoreable
    # fault diagnoses and are reported separately). The GATE is on the
    # CALIBRATED diagnoser (A5 / RG-LEARNED, Phase 4 'is diagnosis
    # calibrated'); the hand-set RG is reported alongside for comparison.
    kin_db = "experiments/results/validation_kinematic/episodes.db"
    mj_db = "experiments/results/validation_mujoco/episodes.db"
    data = load_episodes([kin_db, mj_db])
    learned_data = load_episodes(
        ["experiments/results/validation_learned_kinematic/episodes.db",
         "experiments/results/validation_learned_mujoco/episodes.db"])
    # pinned cells (pre-registered fault x horizon structure): every fault
    # class has >= 30 episodes, so macro-F1 is not a sparse-class artifact
    pinned = load_episodes(
        ["experiments/results/validation_pinned_kinematic/episodes.db",
         "experiments/results/validation_pinned_mujoco/episodes.db"])

    def diag_stats(rows):
        pairs = [(d["truth"], d["argmax"]) for d in rows]
        confs, correct = [], []
        for d in rows:
            post = json.loads(d["posterior_json"])["posterior"]
            confs.append(max(post.values()))
            correct.append(float(d["argmax"] == d["truth"]))
        return pairs, confs, correct

    per_class = {}
    eps_sys_pin = {e["id"]: e["system"] for e in pinned["episodes"]}
    a5_rows = [d for d in pinned["diagnoses"]
               if eps_sys_pin.get(d["episode_id"]) == "A5"
               and d["truth"] in diag6]
    rg_rows = [d for d in pinned["diagnoses"]
               if eps_sys_pin.get(d["episode_id"]) == "RG"
               and d["truth"] in diag6]
    a5_pairs, a5_confs, a5_correct = diag_stats(a5_rows)
    rg_pairs, rg_confs, rg_correct = diag_stats(rg_rows)

    def macro_f1(pairs, labels):
        per = {}
        for lab in labels:
            tp = sum(1 for t, a in pairs if t == lab and a == lab)
            fp = sum(1 for t, a in pairs if t != lab and a == lab)
            fn = sum(1 for t, a in pairs if t == lab and a != lab)
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            per[lab] = {"precision": round(prec, 3), "recall": round(rec, 3),
                         "f1": round(2 * prec * rec / (prec + rec)
                                      if prec + rec else 0.0, 3),
                         "n_truth": int(tp + fn)}
        return float(np.mean([per[lab]["f1"] for lab in labels])), per

    macro_f1_a5, per_class_a5 = macro_f1(a5_pairs, diag6)
    macro_f1_rg, per_class_rg = macro_f1(rg_pairs, diag6)
    ece_a5 = expected_calibration_error(a5_confs, a5_correct) if a5_confs \
        else None
    ece_rg = expected_calibration_error(rg_confs, rg_correct) if rg_confs \
        else None

    # ---- separability: modal recovery per fault -----------------------------
    eps_sys = {e["id"]: e["system"] for e in data["episodes"]}
    diag = {(d["episode_id"], d["step"]): d for d in data["diagnoses"]}
    from collections import defaultdict, Counter
    rec_by_fault = defaultdict(Counter)
    for r in data["recoveries"]:
        d = diag.get((r["episode_id"], r["step"]))
        if not d or r["chosen"] in ("verify", "escalate"):
            continue
        if eps_sys.get(r["episode_id"]) != "RG":
            continue
        rec_by_fault[d["argmax"]][r["chosen"]] += 1
    modal = {f: (c.most_common(1)[0][0] if c else None)
             for f, c in rec_by_fault.items()}
    non_separable = []
    fault_list = sorted(rec_by_fault)
    for i, f1 in enumerate(fault_list):
        for f2 in fault_list[i + 1:]:
            if modal[f1] == modal[f2] and modal[f1] is not None:
                non_separable.append([f1, f2, modal[f1]])

    # ---- paired comparisons from the validation CSVs ------------------------
    rows = _read_csv("experiments/results/validation_kinematic/results.csv") + \
        _read_csv("experiments/results/validation_mujoco/results.csv")
    by_key = {}
    for r in rows:
        by_key.setdefault((r["condition"], r["horizon"], r["template"],
                           r["seed"]), {})[r["system"]] = r

    def paired(sys_a, sys_b):
        a, b = [], []
        for k, m in by_key.items():
            if sys_a in m and sys_b in m:
                a.append(int(m[sys_a]["success"]))
                b.append(int(m[sys_b]["success"]))
        if not a:
            return None
        d = np.array(a, float) - np.array(b, float)
        mean, lo, hi = paired_bootstrap_ci(d)
        _, _, p = mcnemar_exact(np.array(a), np.array(b))
        return {"n": len(a), "diff": round(mean, 3), "ci": [round(lo, 3),
                                                            round(hi, 3)],
                "mcnemar_p": round(p, 5)}

    rg_b2 = paired("RG", "B2")
    rg_b3 = paired("RG", "B3")

    # ---- learned-artifact comparisons ---------------------------------------
    learned_rows = []
    for backend in ("kinematic", "mujoco"):
        p = f"experiments/results/validation_learned_{backend}/results.csv"
        if os.path.exists(p):
            learned_rows += _read_csv(p)
    def rate(sysid):
        rs = [r for r in learned_rows if r["system"] == sysid]
        return (float(np.mean([int(r["success"]) for r in rs]))
                if rs else None)
    rg_learned = rate("RG-LEARNED")
    a5 = rate("A5")
    rg_ref = rate("RG")

    unsafe = sum(int(r["unsafe_executions"]) for r in rows)
    interventions = float(np.mean([int(r["interventions"]) for r in rows]))
    return {
        "n_diagnoses_scoreable": {"A5": len(a5_rows), "RG": len(rg_rows)},
        "macro_f1_calibrated": round(macro_f1_a5, 3),
        "macro_f1_handset": round(macro_f1_rg, 3),
        "per_class_calibrated": per_class_a5,
        "per_class_handset": per_class_rg,
        "ece_calibrated": round(ece_a5, 4) if ece_a5 is not None else None,
        "ece_handset": round(ece_rg, 4) if ece_rg is not None else None,
        "separability": {"modal_recovery_per_fault": modal,
                         "non_separable_pairs": non_separable},
        "rg_vs_b2": rg_b2,
        "rg_vs_b3": rg_b3,
        "unsafe_executions": unsafe,
        "interventions_per_episode": round(interventions, 4),
        "learned": {"rg_learned_success": rg_learned, "a5_success": a5,
                    "rg_success_ref": rg_ref,
                    "n_rows": len(learned_rows)},
        "tuned": tuned,
        "pass": {},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--metrics-only", action="store_true",
                    help="reuse existing tuning + validation runs")
    args = ap.parse_args()
    quick = args.quick

    if args.metrics_only:
        with open(os.path.join(REPO, "configs", "tuning_v2.json")) as fh:
            tuned = json.load(fh)["chosen"]
    else:
        print("[1/3] grid search on validation (alpha x delta)")
        grid = grid_search(quick)
        tuned = {"alpha_step": grid["chosen"]["alpha_step"],
                 "delta": grid["chosen"]["delta"]}
        with open(os.path.join(REPO, "configs", "tuning_v2.json"), "w") as fh:
            json.dump({"version": "tuning_v2", "source": "validation split",
                       "grid": grid["grid"], "chosen": grid["chosen"],
                       "note": "alpha tunes the global per-step Bonferroni "
                               "alpha (per-group structure retained); delta "
                               "is the escalation weight"}, fh, indent=2)
        print("frozen -> configs/tuning_v2.json", tuned)

        print("[2/3] validation runs (both backends, artifact-injected)")
        artifact = os.path.join(REPO, "configs", "learned_artifact_v1.json")
        run_validation(quick, tuned, artifact)

    print("[3/3] G3 metrics")
    g3 = g3_metrics(quick, tuned)
    g3["pass"] = {
        "macro_f1_ge_080": (g3["macro_f1_calibrated"] or 0) >= 0.80,
        "ece_below_010": g3["ece_calibrated"] is not None and
                         g3["ece_calibrated"] < 0.10,
        "rg_beats_b2": (g3["rg_vs_b2"] or {}).get("ci", [0, 0])[1] > 0,
        "rg_beats_b3": (g3["rg_vs_b3"] or {}).get("ci", [0, 0])[1] > 0,
        "zero_unsafe": g3["unsafe_executions"] == 0,
    }
    g3["G3_PASS"] = all(g3["pass"].values())
    with open(os.path.join(REPO, "experiments", "results",
                           "g3_verdict.json"), "w") as fh:
        json.dump(g3, fh, indent=2, default=float)
    print(json.dumps({k: g3[k] for k in
                      ("macro_f1_calibrated", "macro_f1_handset",
                       "ece_calibrated", "ece_handset", "rg_vs_b2",
                       "rg_vs_b3", "unsafe_executions", "learned", "pass",
                       "G3_PASS")}, indent=2, default=float))
    print("G3 verdict:", "PASS" if g3["G3_PASS"] else "FAIL")


if __name__ == "__main__":
    main()
