"""M6: formal pre-registered evaluation - analysis and go/no-go.

Loads the frozen test-split runs (both backends) and computes the
pre-registered statistics (docs/pre_registration.md section 4):
  - RG vs each baseline on success: paired bootstrap 10k CI + McNemar exact
  - H2 horizon slopes (OLS) + bootstrap CI of slope differences (RG - B2/B3)
  - Ablations A1-A5: McNemar + Holm-Bonferroni across the family
  - Extra actions: Wilcoxon signed-rank vs B2/B3
  - Regret, false-alarm rate, unsafe actions, escalations per system
  - Diagnosis: confusion matrix (as measured), macro-F1, ECE, Brier
  - Per-horizon and per-fault tables (never only pooled)
  - The five go/no-go criteria (pre-registration section 5)

Run: python -m scripts.formal_eval [--dirs DIRS...] [--out experiments/results/formal]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from collections import Counter, defaultdict

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import sys  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.stats import (brier_score, expected_calibration_error,  # noqa: E402
                                holm_bonferroni, mcnemar_exact,
                                paired_bootstrap_ci, slope_least_squares,
                                wilcoxon_signed_rank, wilson_ci)
from rg_core.learning import load_episodes  # noqa: E402
from rg_core.types import FAULTS  # noqa: E402

BASELINES = ["B1", "B2", "B3", "B4", "B5"]
ABLATIONS = ["A1", "A2", "A3", "A4", "A5"]
ALL_SYSTEMS = BASELINES + ["RG"] + ABLATIONS + ["A6"]
HORIZONS = [1, 2, 3, 5, 7, 10]
FAULTS7 = ["f_miss", "f_slip", "f_pose", "f_collision", "f_perception",
           "f_controller", "stale_dependency"]
DIAG6 = [f for f in FAULTS if f != "f_none"]
COLORS = {"B1": "#999999", "B2": "#4c78a8", "B3": "#f58518", "B4": "#54a24b",
          "B5": "#b279a2", "RG": "#e45756", "A1": "#72b7b2", "A2": "#cab2d6",
          "A3": "#ffbf7b", "A4": "#8cd17d", "A5": "#f2f2a0", "A6": "#bde0c8"}


def read_rows(dirs):
    rows = []
    for d in dirs:
        path = os.path.join(d, "results.csv")
        with open(path) as fh:
            backend = json.load(open(os.path.join(d, "run_meta.json")))["backend"]
            for r in csv.DictReader(fh):
                if r.get("success", "") == "":
                    continue
                r["backend"] = backend
                rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", nargs="+",
                    default=["experiments/results/formal_kinematic",
                             "experiments/results/formal_mujoco"])
    ap.add_argument("--out", default="experiments/results/formal")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    rows = read_rows(args.dirs)
    injected = [r for r in rows if r["faults"] != "none"]
    uninjectable = [r for r in rows if r["faults"] == "none"]

    def rows_for(system, horizon=None, backend=None):
        out = []
        for r in injected:
            if r["system"] != system:
                continue
            if horizon is not None and int(r["horizon"]) != horizon:
                continue
            if backend is not None and r["backend"] != backend:
                continue
            out.append(r)
        return out

    def key(r):
        return (r["backend"], r["horizon"], r["faults"], r["seed"])

    def paired(system_a, system_b, field="success"):
        amap = {key(r): r for r in rows_for(system_a)}
        bmap = {key(r): r for r in rows_for(system_b)}
        ks = sorted(set(amap) & set(bmap))
        a = [float(amap[k][field] or 0) for k in ks]
        b = [float(bmap[k][field] or 0) for k in ks]
        return ks, np.array(a), np.array(b)

    report = {"n_rows_total": len(rows),
              "n_injected_episodes": len(injected),
              "n_uninjectable_episodes": len(uninjectable),
              "per_backend": Counter(r["backend"] for r in rows),
              "uninjectable_by_fault_horizon": {}}

    # ---------------- per-system summaries + confounders ----------------
    per_sys = {}
    for s in ALL_SYSTEMS:
        rs = rows_for(s)
        if not rs:
            continue
        succ = [int(r["success"]) for r in rs]
        ff = [r for r in rs if r["faults"] == "none"]
        per_sys[s] = {
            "n": len(rs),
            "success": round(float(np.mean(succ)), 4),
            "success_wilson": [round(x, 4) for x in wilson_ci(sum(succ), len(succ))],
            "extra_actions": round(float(np.mean(
                [float(r["extra_actions"]) for r in rs])), 3),
            "recovery_time_s": round(float(np.mean(
                [float(r["recovery_time_s"]) for r in rs])), 3),
            "regret": round(float(np.mean([float(r["regret"] or 0)
                                           for r in rs])), 3),
            "fa_rate": round(
                float(sum(int(r["flags_fault_free"] or 0) for r in rs) /
                      max(1, sum(int(r["steps"] or 0) for r in rs))), 4),
            "extra_actions_success_only": round(float(np.mean(
                [float(r["extra_actions"]) for r in rs if int(r["success"])]))
                if any(int(r["success"]) for r in rs) else None or 0.0, 3),
            "unsafe_executions": int(sum(int(r["unsafe_executions"]) for r in rs)),
            "escalations": int(sum(int(r["escalations"]) for r in rs)),
            "interventions": int(sum(int(r["interventions"]) for r in rs)),
        }
    report["per_system"] = per_sys

    # ---------------- primary comparisons (prereg section 4) --------------
    comparisons = {}
    for base in BASELINES:
        ks, a, b = paired("RG", base)
        mean, lo, hi = paired_bootstrap_ci(a - b)
        _, _, p = mcnemar_exact(a, b)
        comparisons[f"RG-{base}"] = {
            "n": len(a), "diff": round(mean, 4),
            "ci95": [round(lo, 4), round(hi, 4)],
            "mcnemar_p": round(p, 6), "significant": lo > 0 or hi < 0}
    report["primary_comparisons"] = comparisons

    # ---------------- H2: horizon slopes ------------------------------------
    slopes = {}
    for s in ALL_SYSTEMS:
        if not rows_for(s):
            continue
        xs, ys = [], []
        for h in HORIZONS:
            rs = rows_for(s, horizon=h)
            if rs:
                xs.append(h)
                ys.append(float(np.mean([int(r["success"]) for r in rs])))
        slopes[s] = round(slope_least_squares(xs, ys), 5)
    report["horizon_slopes_per_system"] = slopes

    def slope_diff_ci(sys_a, sys_b, n_boot=10000):
        rng = np.random.default_rng(7)
        diffs = []
        by_h = {}
        for h in HORIZONS:
            ka, a, _ = paired(sys_a, sys_b)
            ah = np.array([int(r["success"]) for r in rows_for(sys_a, h)])
            bh = np.array([int(r["success"]) for r in rows_for(sys_b, h)])
            if len(ah) and len(bh):
                by_h[h] = (ah, bh)
        for _ in range(n_boot):
            xs, ya, yb = [], [], []
            for h, (ah, bh) in by_h.items():
                n = len(ah)
                idx = rng.integers(0, n, n)
                xs.append(h)
                ya.append(ah[idx].mean())
                yb.append(bh[idx].mean())
            diffs.append(slope_least_squares(xs, ya) -
                         slope_least_squares(xs, yb))
        diffs = np.array(diffs)
        lo, hi = np.quantile(diffs, [0.025, 0.975])
        return float(np.mean(diffs)), float(lo), float(hi)

    slope_diffs = {}
    for base in ("B2", "B3", "B1", "B4", "B5"):
        m, lo, hi = slope_diff_ci("RG", base)
        slope_diffs[f"RG-{base}"] = {"mean": round(m, 5),
                                     "ci95": [round(lo, 5), round(hi, 5)],
                                     "significant": lo > 0 or hi < 0}
    report["h2_slope_differences"] = slope_diffs

    # ---------------- ablations (H5) ----------------------------------------
    abl = {}
    pvals = []
    for a in ABLATIONS:
        ks, x, y = paired("RG", a)
        _, _, p = mcnemar_exact(x, y)
        mean, lo, hi = paired_bootstrap_ci(x - y)
        abl[f"RG-{a}"] = {"n": len(x), "diff": round(mean, 4),
                          "ci95": [round(lo, 4), round(hi, 4)],
                          "mcnemar_p": round(p, 6)}
        pvals.append(p)
    adj = holm_bonferroni(pvals)
    for (name, d), a in zip(abl.items(), adj):
        d["holm_p"] = round(a, 6)
    report["ablations_h5"] = abl

    # ---------------- extra actions (H3) ------------------------------------
    ea = {}
    for base in ("B2", "B3"):
        ks, x, y = paired("RG", base, field="extra_actions")
        stat, p = wilcoxon_signed_rank(x, y)
        ea[f"RG-{base}"] = {"mean_rg": round(float(np.mean(x)), 3),
                            "mean_base": round(float(np.mean(y)), 3),
                            "wilcoxon_p": round(p, 6)}
    report["extra_actions_h3"] = ea
    # secondary analysis (labelled: post-hoc, not a pre-registered criterion):
    # extra actions among SUCCEEDED episodes only - B2's low unconditional
    # mean comes from aborted episodes (truncated action counts)
    ea_succ = {}
    for base in ("B2", "B3"):
        ks, x, y = paired("RG", base, field="extra_actions")
        xs, ys = [], []
        for k, xx, yy in zip(ks, x, y):
            # keep both systems' action counts only where BOTH succeeded
            amap = {key(r): r for r in rows_for("RG")}
            bmap = {key(r): r for r in rows_for(base)}
            if int(amap[k]["success"]) and int(bmap[k]["success"]):
                xs.append(xx)
                ys.append(yy)
        if xs:
            ea_succ[f"RG-{base}"] = {
                "n_both_success": len(xs),
                "mean_rg": round(float(np.mean(xs)), 3),
                "mean_base": round(float(np.mean(ys)), 3)}
    report["extra_actions_success_only_secondary"] = ea_succ

    # ---------------- diagnosis quality --------------------------------------
    data = load_episodes([os.path.join(d, "episodes.db") for d in args.dirs])
    eps_sys = {e["id"]: e["system"] for e in data["episodes"]}
    diag_quality = {}
    for sysid in ("RG", "A5"):
        pairs, confs, correct = [], [], []
        for d in data["diagnoses"]:
            if eps_sys.get(d["episode_id"]) != sysid:
                continue
            if d["truth"] not in DIAG6:
                continue
            pairs.append((d["truth"], d["argmax"]))
            post = json.loads(d["posterior_json"])["posterior"]
            confs.append(max(post.values()))
            correct.append(float(d["argmax"] == d["truth"]))
        if not pairs:
            continue
        conf = np.zeros((7, 7))
        fi = {f: i for i, f in enumerate(FAULTS)}
        for t, a in pairs:
            conf[fi[t], fi[a]] += 1
        macro = []
        per_class = {}
        for lab in DIAG6:
            tp = sum(1 for t, a in pairs if t == lab and a == lab)
            fp = sum(1 for t, a in pairs if t != lab and a == lab)
            fn = sum(1 for t, a in pairs if t == lab and a != lab)
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
            per_class[lab] = {"precision": round(prec, 3),
                              "recall": round(rec, 3), "f1": round(f1, 3)}
            macro.append(f1)
        diag_quality[sysid] = {
            "n_scoreable": len(pairs),
            "accuracy": round(float(np.mean(correct)), 4),
            "macro_f1": round(float(np.mean(macro)), 4),
            "per_class": per_class,
            "ece": round(expected_calibration_error(confs, correct), 4),
            "brier": round(brier_score(confs, correct), 4),
            "confusion_matrix": conf.astype(int).tolist(),
        }
    report["diagnosis_quality"] = diag_quality

    # ---------------- per-horizon / per-fault tables ------------------------
    ph = {}
    for s in ALL_SYSTEMS:
        if not rows_for(s):
            continue
        ph[s] = {}
        for h in HORIZONS:
            rs = rows_for(s, horizon=h)
            if rs:
                k = sum(int(r["success"]) for r in rs)
                ph[s][h] = [k, len(rs), round(k / len(rs), 3)]
    report["per_horizon_success"] = ph

    pf = {}
    for s in ("RG", "B2", "B3", "B5"):
        if not rows_for(s):
            continue
        pf[s] = {}
        for f in FAULTS7:
            rs = [r for r in rows_for(s) if r["faults"] == f]
            if rs:
                pf[s][f] = round(float(np.mean([int(r["success"])
                                                for r in rs])), 3)
    report["per_fault_success"] = pf

    # ---------------- go / no-go ---------------------------------------------
    c = comparisons
    go = {
        "1_rg_beats_b2_b3_ci_excludes_0":
            c["RG-B2"]["significant"] and c["RG-B3"]["significant"],
        "2_horizon_slope_widens":
            slope_diffs["RG-B2"]["significant"] or
            slope_diffs["RG-B3"]["significant"],
        "3_extra_actions_below_b3_not_above_b2":
            per_sys["RG"]["extra_actions"] < per_sys["B3"]["extra_actions"]
            and per_sys["RG"]["extra_actions"] <=
            per_sys["B2"]["extra_actions"] + 1e-9,
        "4_unsafe_le_every_baseline_zero_hard":
            all(per_sys["RG"]["unsafe_executions"] <=
                per_sys[b]["unsafe_executions"] for b in BASELINES)
            and per_sys["RG"]["unsafe_executions"] == 0,
        "5_a1_drop_holm_p_lt_0.05":
            abl["RG-A1"]["holm_p"] < 0.05,
    }
    report["go_no_go"] = {k: bool(v) for k, v in go.items()}
    report["go_no_go"]["ALL_CRITERIA_MET"] = all(go.values())

    with open(os.path.join(args.out, "go_no_go.json"), "w") as fh:
        json.dump(report, fh, indent=2, default=float)

    # ---------------- figures ------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    for ax, backend in zip(axes, ("kinematic", "mujoco")):
        for s in ("B1", "B2", "B3", "B4", "B5", "RG"):
            xs, ys = [], []
            for h in HORIZONS:
                rs = rows_for(s, horizon=h, backend=backend)
                if rs:
                    xs.append(h)
                    ys.append(float(np.mean([int(r["success"]) for r in rs])))
            ax.plot(xs, ys, marker="o", label=s, color=COLORS.get(s))
        ax.set_xlabel("horizon (subgoals)")
        ax.set_ylabel("recovery success")
        ax.set_title(f"{backend} backend")
        ax.set_ylim(0, 1.02)
        ax.grid(alpha=0.3)
    axes[0].legend(loc="lower left", fontsize=8)
    fig.savefig(os.path.join(args.out, "fig_success_horizon.png"), dpi=150)
    plt.close(fig)

    if "RG" in diag_quality:
        fig, ax = plt.subplots(figsize=(4.6, 4), constrained_layout=True)
        conf = np.array(diag_quality["RG"]["confusion_matrix"])
        confn = conf / np.maximum(1, conf.sum(axis=1, keepdims=True))
        im = ax.imshow(confn, cmap="Blues", vmin=0, vmax=1)
        ax.set_xticks(range(7), FAULTS, rotation=45, ha="right", fontsize=7)
        ax.set_yticks(range(7), FAULTS, fontsize=7)
        ax.set_xlabel("argmax posterior")
        ax.set_ylabel("injected truth")
        ax.set_title("RG diagnosis confusion (row-normalised)")
        fig.colorbar(im, ax=ax, fraction=0.046)
        fig.savefig(os.path.join(args.out, "fig_confusion.png"), dpi=150)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(4.2, 3.6), constrained_layout=True)
        for sysid, col in (("RG", "#e45756"), ("A5", "#f2c14e")):
            if sysid not in diag_quality:
                continue
            d = diag_quality[sysid]
            # rebuild bin points from raw: reuse ECE bins
            confs, correct = [], []
            for dd in data["diagnoses"]:
                if eps_sys.get(dd["episode_id"]) != sysid:
                    continue
                if dd["truth"] not in DIAG6:
                    continue
                post = json.loads(dd["posterior_json"])["posterior"]
                confs.append(max(post.values()))
                correct.append(float(dd["argmax"] == dd["truth"]))
            confs = np.array(confs)
            correct = np.array(correct)
            bins = np.linspace(0, 1, 11)
            centers, accs = [], []
            for i in range(10):
                m = (confs > bins[i]) & (confs <= bins[i + 1]) if i else \
                    (confs >= 0) & (confs <= bins[1])
                if m.sum() >= 3:
                    centers.append((bins[i] + bins[i + 1]) / 2)
                    accs.append(correct[m].mean())
            ax.plot(centers, accs, marker="o", label=sysid, color=col)
        ax.plot([0, 1], [0, 1], ls="--", c="gray", lw=0.8)
        ax.set_xlabel("posterior confidence")
        ax.set_ylabel("empirical accuracy")
        ax.set_title("diagnosis reliability (test split)")
        ax.legend(fontsize=8)
        fig.savefig(os.path.join(args.out, "fig_calibration.png"), dpi=150)
        plt.close(fig)

    # ---------------- console summary ----------------------------------------
    print(json.dumps({
        "per_system": {s: {k: per_sys[s][k] for k in
                           ("n", "success", "extra_actions", "regret",
                            "fa_rate", "unsafe_executions")}
                       for s in per_sys},
        "primary_comparisons": comparisons,
        "h2_slope_differences": slope_diffs,
        "ablations_h5": abl,
        "diagnosis_quality": {k: {kk: diag_quality[k][kk] for kk in
                                  ("macro_f1", "accuracy", "ece", "brier")}
                              for k in diag_quality},
        "go_no_go": report["go_no_go"],
    }, indent=2, default=float))
    print(f"report -> {args.out}/go_no_go.json")


if __name__ == "__main__":
    main()
