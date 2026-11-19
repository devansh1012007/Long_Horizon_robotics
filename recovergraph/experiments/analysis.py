"""Result analysis (spec 10.5-10.6, Section 3 go/no-go): per-cell tables,
paired statistics RG vs baselines, ablation deltas with Holm correction,
diagnosis confusion matrix and calibration, plots.

Usage:
  python -m experiments.analysis --results experiments/results/smoke
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402
import numpy as np                # noqa: E402

from experiments.stats import (brier_score, expected_calibration_error,
                               holm_bonferroni, mcnemar_exact,
                               paired_bootstrap_ci, slope_least_squares,
                               wilcoxon_signed_rank, wilson_ci)  # noqa: E402
from rg_core.types import FAULTS  # noqa: E402

SYSTEM_ORDER = ["B1", "B2", "B3", "B4", "B5", "RG"]
ABLATIONS = ["A1", "A2", "A3", "A4"]
COLORS = {"B1": "#999999", "B2": "#4c78a8", "B3": "#f58518", "B4": "#54a24b",
          "B5": "#b279a2", "RG": "#e45756", "A1": "#72b7b2", "A2": "#cab2d6",
          "A3": "#ffbf7b", "A4": "#8cd17d"}


def load_rows(results_dir: str) -> list:
    with open(os.path.join(results_dir, "results.csv")) as fh:
        return [r for r in csv.DictReader(fh)
                if r.get("success", "") != ""]


def group(rows, key_fn):
    out = defaultdict(list)
    for r in rows:
        out[key_fn(r)].append(r)
    return out


def success_rate(rows):
    n = len(rows)
    k = sum(int(r["success"]) for r in rows)
    p, lo, hi = wilson_ci(k, n)
    return k, n, p, lo, hi


def paired_vectors(rows_a, rows_b, field):
    """Pair by (condition, horizon, template, seed)."""
    key = lambda r: (r["condition"], r["horizon"], r["template"], r["seed"])
    bm = {key(r): r for r in rows_b}
    a, b = [], []
    for r in rows_a:
        m = bm.get(key(r))
        if m is None:
            continue
        if field == "success":
            a.append(int(r["success"]))
            b.append(int(m["success"]))
        else:
            a.append(float(r[field] or 0.0))
            b.append(float(m[field] or 0.0))
    return a, b


def write_summary(rows, out_dir):
    lines = ["# RecoverGraph smoke results (milestone 1)", "",
             "> Smoke run, reduced N - NOT the pre-registered test set.", ""]

    # per-cell table
    lines += ["## End-to-end success by cell (Wilson 95% CI)", "",
              "| system | condition | horizon | success | rate | 95% CI | extra actions | interventions |",
              "|---|---|---|---|---|---|---|---|"]
    cells = group(rows, lambda r: (r["system"], r["condition"], r["horizon"]))
    for (sysid, cond, h), rs in sorted(cells.items()):
        k, n, p, lo, hi = success_rate(rs)
        ea = np.mean([float(r["extra_actions"]) for r in rs])
        iv = np.mean([float(r["interventions"]) for r in rs])
        lines.append(f"| {sysid} | {cond} | {h} | {k}/{n} | {p:.2f} | "
                     f"[{lo:.2f}, {hi:.2f}] | {ea:.1f} | {iv:.2f} |")

    # paired comparisons vs RG
    by_sys = group(rows, lambda r: r["system"])
    lines += ["", "## Paired comparisons: RG vs baselines (single-fault episodes)",
              "", "| comparison | RG | baseline | diff (RG-base) | 95% CI | "
              "McNemar p | Wilcoxon p |", "|---|---|---|---|---|---|---|"]
    faulted = [r for r in rows if r["condition"] == "single"]
    stats_block = {}
    rg_rows = [r for r in faulted if r["system"] == "RG"]
    for base in ("B1", "B2", "B3", "B4", "B5"):
        if base not in by_sys or not rg_rows:
            continue
        a, b = paired_vectors(rg_rows, [r for r in faulted
                                        if r["system"] == base], "success")
        if not a:
            continue
        mean, lo, hi = paired_bootstrap_ci(np.array(a) - np.array(b))
        _b, _c, pm = mcnemar_exact(a, b)
        _s, pw = wilcoxon_signed_rank(a, b)
        lines.append(f"| RG - {base} | {np.mean(a):.2f} | {np.mean(b):.2f} | "
                     f"{mean:+.2f} | [{lo:+.2f}, {hi:+.2f}] | {pm:.3g} | {pw:.3g} |")
        stats_block[base] = {"diff": mean, "ci": [lo, hi], "mcnemar_p": pm,
                             "wilcoxon_p": pw}

    # ablation deltas with Holm correction
    if rg_rows:
        raw_p, deltas, names = [], [], []
        for ab in ABLATIONS:
            if ab not in by_sys:
                continue
            a, b = paired_vectors(rg_rows, [r for r in faulted
                                            if r["system"] == ab], "success")
            if not a:
                continue
            _b, _c, p = mcnemar_exact(a, b)
            raw_p.append(p)
            deltas.append(float(np.mean(a) - np.mean(b)))
            names.append(f"RG - {ab}")
        if raw_p:
            adj = holm_bonferroni(raw_p)
            lines += ["", "## Ablation deltas (Holm-Bonferroni)", "",
                      "| comparison | delta | raw p | adjusted p |", "|---|---|---|---|"]
            for nm, d, rp, ap in zip(names, deltas, raw_p, adj):
                lines.append(f"| {nm} | {d:+.2f} | {rp:.3g} | {ap:.3g} |")
            stats_block["ablation_adjusted_p"] = dict(zip(names, adj))

    # success vs horizon slopes
    lines += ["", "## Success-vs-horizon slope (single-fault)", "",
              "| system | slope (pp per subgoal) |", "|---|---|"]
    slopes = {}
    for sysid, rs in sorted(by_sys.items()):
        fr = [r for r in rs if r["condition"] == "single"]
        if not fr:
            continue
        per_h = group(fr, lambda r: r["horizon"])
        xs = sorted(per_h)
        ys = [np.mean([int(r["success"]) for r in per_h[x]]) for x in xs]
        if len(xs) >= 2:
            slopes[sysid] = slope_least_squares(xs, ys)
            lines.append(f"| {sysid} | {100 * slopes[sysid]:+.1f} |")

    # diagnosis quality + false alarms
    diag_rows = [r for r in rows if r["diag_correct"] != ""]
    if diag_rows:
        acc = np.mean([int(r["diag_correct"]) for r in diag_rows])
        lines += ["", f"- Diagnosis accuracy (argmax = injected label): "
                      f"**{acc:.2f}** over {len(diag_rows)} scored episodes"]
        stats_block["diagnosis_accuracy"] = acc
    fa_flags = sum(int(r["flags_fault_free"]) for r in rows
                   if r["condition"] == "none")
    fa_steps = sum(int(r["steps"]) for r in rows if r["condition"] == "none")
    if fa_steps:
        lines.append(f"- False-alarm rate on fault-free steps: "
                     f"**{fa_flags / fa_steps:.3f}** ({fa_flags}/{fa_steps}) "
                     f"(target < 0.02)")
        stats_block["false_alarm_rate"] = fa_flags / fa_steps
    regret = [float(r["regret"]) for r in rows
              if r["condition"] == "single" and r["system"] == "RG"]
    if regret:
        lines.append(f"- Mean recovery regret (RG): "
                     f"**{np.mean(regret):.2f}** cost units")
        stats_block["mean_regret"] = float(np.mean(regret))

    with open(os.path.join(out_dir, "summary.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(os.path.join(out_dir, "stats.json"), "w") as fh:
        json.dump(stats_block, fh, indent=2)
    return lines


def confusion_from_db(results_dir: str):
    """Diagnosis confusion matrix + calibration from the episode DB."""
    import sqlite3
    db_path = os.path.join(results_dir, "episodes.db")
    if not os.path.exists(db_path):
        return None, None, None
    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT posterior_json, argmax, truth FROM diagnoses")
    labels = FAULTS
    idx = {f: i for i, f in enumerate(labels)}
    cm = np.zeros((len(labels), len(labels)), int)
    confs, cors = [], []
    for post_json, argmax, truth in cur:
        if truth not in idx:
            continue
        cm[idx[truth], idx.get(argmax, len(labels) - 1)] += 1
        post = json.loads(post_json)
        confs.append(post.get(argmax, 0.0))
        cors.append(1.0 if argmax == truth else 0.0)
    conn.close()
    return cm, confs, cors


def macro_f1(cm: np.ndarray) -> float:
    f1s = []
    for i in range(cm.shape[0]):
        tp = cm[i, i]
        fp = cm[:, i].sum() - tp
        fn = cm[i, :].sum() - tp
        if tp == 0 and (fp or fn):
            f1s.append(0.0)
        elif tp == 0:
            f1s.append(1.0)
        else:
            f1s.append(2 * tp / (2 * tp + fp + fn))
    return float(np.mean(f1s))


def make_plots(rows, results_dir):
    by_sys = group(rows, lambda r: r["system"])
    # 1. success vs horizon
    fig, ax = plt.subplots(figsize=(6.4, 4.2), constrained_layout=True)
    for sysid in SYSTEM_ORDER + ABLATIONS:
        if sysid not in by_sys:
            continue
        fr = [r for r in by_sys[sysid] if r["condition"] == "single"]
        if not fr:
            continue
        per_h = group(fr, lambda r: r["horizon"])
        xs = sorted(per_h)
        ys = [np.mean([int(r["success"]) for r in per_h[x]]) for x in xs]
        ax.plot(xs, ys, marker="o", label=sysid,
                color=COLORS.get(sysid), lw=2 if sysid == "RG" else 1.2)
    ax.set_xlabel("task horizon (subgoals)")
    ax.set_ylabel("end-to-end success")
    ax.set_title("Success vs horizon (single-fault episodes, smoke run)")
    ax.set_ylim(-0.02, 1.05)
    ax.grid(alpha=0.3)
    ax.legend(ncols=2, fontsize=8)
    fig.savefig(os.path.join(results_dir, "fig_success_vs_horizon.png"), dpi=150)
    plt.close(fig)

    # 2. extra actions bar (single-fault, all horizons pooled)
    fig, ax = plt.subplots(figsize=(6.4, 3.6), constrained_layout=True)
    names, vals = [], []
    for sysid in SYSTEM_ORDER + ABLATIONS:
        fr = [r for r in by_sys.get(sysid, []) if r["condition"] == "single"]
        if fr:
            names.append(sysid)
            vals.append(float(np.mean([float(r["extra_actions"]) for r in fr])))
    ax.bar(names, vals, color=[COLORS.get(n, "#888") for n in names])
    ax.set_ylabel("mean extra actions per episode")
    ax.set_title("Recovery cost (single-fault episodes, smoke run)")
    ax.grid(alpha=0.3, axis="y")
    fig.savefig(os.path.join(results_dir, "fig_extra_actions.png"), dpi=150)
    plt.close(fig)

    # 3. confusion + reliability
    cm, confs, cors = confusion_from_db(results_dir)
    if cm is not None and cm.sum() > 0:
        fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.2),
                                 constrained_layout=True)
        im = axes[0].imshow(cm, cmap="Blues")
        axes[0].set_xticks(range(len(FAULTS)))
        axes[0].set_xticklabels(FAULTS, rotation=45, ha="right", fontsize=7)
        axes[0].set_yticks(range(len(FAULTS)))
        axes[0].set_yticklabels(FAULTS, fontsize=7)
        axes[0].set_xlabel("diagnosed (argmax)")
        axes[0].set_ylabel("injected (truth)")
        axes[0].set_title(f"Diagnosis confusion (macro-F1 {macro_f1(cm):.2f})")
        fig.colorbar(im, ax=axes[0], shrink=0.8)
        if confs:
            bins = np.linspace(0, 1, 11)
            ids = np.digitize(confs, bins) - 1
            xs, ys = [], []
            for b in range(10):
                m = [i for i in range(len(confs)) if ids[i] == b]
                if m:
                    xs.append(np.mean([confs[i] for i in m]))
                    ys.append(np.mean([cors[i] for i in m]))
            axes[1].plot([0, 1], [0, 1], "k--", lw=1, label="perfect")
            axes[1].plot(xs, ys, "o-", color="#e45756", label="diagnoser")
            axes[1].set_xlabel("posterior confidence (argmax)")
            axes[1].set_ylabel("empirical correctness")
            axes[1].set_title("Reliability (ECE "
                              f"{expected_calibration_error(confs, cors):.3f}, "
                              f"Brier {brier_score(confs, cors):.3f})")
            axes[1].grid(alpha=0.3)
            axes[1].legend(fontsize=8)
        fig.savefig(os.path.join(results_dir, "fig_diagnosis.png"), dpi=150)
        plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="experiments/results/smoke")
    args = ap.parse_args()
    rows = load_rows(args.results)
    write_summary(rows, args.results)
    make_plots(rows, args.results)
    # integrity of figures
    from PIL import Image
    for f in os.listdir(args.results):
        if f.endswith(".png"):
            img = Image.open(os.path.join(args.results, f))
            arr = np.asarray(img.convert("L"))
            nonwhite = float((arr < 245).mean())
            print(f"{f}: {img.size}, non-white {nonwhite:.2%}")


if __name__ == "__main__":
    main()
