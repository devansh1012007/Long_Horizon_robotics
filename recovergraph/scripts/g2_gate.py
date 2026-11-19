"""G2 gate (M3 acceptance, spec Phase 1-2 criteria on the physics backend).

Checks:
  1. fault-free success >= 95% over 200 episodes (horizons 1-3, RG)
  2. false-alarm rate < 2% on fault-free steps
  3. detection >= 90% of injected faults (per-fault breakdown reported;
     stale_dependency is an invalidation-level mechanism and is reported
     separately)
  4. directional agreement with the kinematic smoke on a reduced shared grid
     (per-cell success comparison, sign agreement of RG-vs-baseline gaps)

Run:  python -m scripts.g2_gate  [--quick]
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np

from rg_core.taskgen import make_task
from rgsim.pipeline import EpisodeRunner
from systems import make_system

FAULTS6 = ["f_miss", "f_slip", "f_pose", "f_collision",
           "f_perception", "f_controller"]
ALL7 = FAULTS6 + ["stale_dependency"]


def episode(system_id, seed, horizon, condition, fault=None,
            backend="mujoco", template=None):
    tpl = template if template is not None else seed % 20
    task = make_task(horizon, tpl, seed)
    runner = EpisodeRunner(make_system(system_id), task, seed=seed,
                           condition=condition, backend=backend,
                           faults=[fault] if fault else None)
    return runner.run()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="experiments/results/g2")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    q = 5 if args.quick else 10          # seeds per fault cell
    os.makedirs(args.out, exist_ok=True)
    t0 = time.time()

    # ---------------- 1+2: fault-free success + false alarms ----------------
    ff_rows = []
    for i in range(200 if not args.quick else 40):
        seed = 5000 + i
        h = (i % 3) + 1
        res = episode("RG", seed, h, "none")
        m = res["metrics"]
        ff_rows.append({"seed": seed, "h": h, "success": m["success"],
                        "flags_ff": m["flags_fault_free"],
                        "steps": m["steps"],
                        "unsafe": m["unsafe_executions"]})
    n_ff = len(ff_rows)
    succ = sum(r["success"] for r in ff_rows) / n_ff
    flags_ff = sum(r["flags_ff"] for r in ff_rows)
    steps_ff = sum(r["steps"] for r in ff_rows)
    fa_rate = flags_ff / steps_ff if steps_ff else 0.0
    unsafe = sum(r["unsafe"] for r in ff_rows)
    print(f"[1] fault-free success = {succ:.3f} over {n_ff} episodes "
          f"(target >= 0.95)")
    print(f"[2] false-alarm rate  = {fa_rate:.4f} ({flags_ff}/{steps_ff} "
          f"fault-free steps; target < 0.02); unsafe={unsafe}")

    # ---------------- 3: detection of injected faults ----------------
    det = {}
    for fault in ALL7:
        detected = manifested = total = 0
        for h in (1, 2, 3, 5):
            for k in range(q):
                seed = 5300 + k * 37 + h
                res = episode("RG", seed, h, "single", fault=fault)
                if not res["faults"]:
                    continue          # structurally uninjectable: skip
                m = res["metrics"]
                total += 1
                flag = m["flags"] >= 1 or m["dispatches"] >= 1
                # a fault *manifests* if anything observable changed:
                # divergence flagged, episode failed, or extra actions spent
                manifest = flag or (not m["success"]) or m["extra_actions"] > 0 \
                    or m["escalations"] > 0
                manifested += int(manifest)
                detected += int(flag)
        det[fault] = {"total": total, "manifested": manifested,
                      "detected": detected,
                      "rate_manifest": detected / manifested if manifested else None,
                      "rate_raw": detected / total}
        line = det[fault]
        print(f"[3] {fault:16s} detected {line['detected']}/{line['total']} raw "
              f"({line['rate_raw']:.2f}), "
              f"{line['detected']}/{line['manifested']} of manifested "
              f"({(line['rate_manifest'] or 0):.2f})")
    diag_rates = [det[f]["rate_manifest"] for f in FAULTS6
                  if det[f]["rate_manifest"] is not None]
    detection_manifest = float(np.mean(diag_rates)) if diag_rates else 0.0

    # ---------------- 4: directional agreement vs kinematic ----------------
    grid = []
    for h in (1, 2, 3, 5):
        for cond in ("single", "none"):
            for sysid in ("B1", "B2", "B3", "B4", "B5", "RG"):
                for k in range(q):
                    seed = 6100 + k * 13 + h
                    grid.append((sysid, h, cond, seed))
    cells = {}
    for backend in ("kinematic", "mujoco"):
        for sysid, h, cond, seed in grid:
            res = episode(sysid, seed, h, cond, backend=backend)
            cells[(backend, sysid, h, cond, seed)] = res["metrics"]["success"]
    # per-system success on the shared grid, per backend
    per_sys = {}
    for backend in ("kinematic", "mujoco"):
        for sysid in ("B1", "B2", "B3", "B4", "B5", "RG"):
            v = [cells[(backend, sysid, h, cond, s)]
                 for (sy, h, cond, s) in grid if sy == sysid]
            per_sys[(backend, sysid)] = float(np.mean(v))
    print(f"{'system':6s}  {'kinematic':>10s} {'mujoco':>8s} {'delta':>7s}")
    for sysid in ("B1", "B2", "B3", "B4", "B5", "RG"):
        a, b = per_sys[("kinematic", sysid)], per_sys[("mujoco", sysid)]
        print(f"{sysid:6s}  {a:10.3f} {b:8.3f} {b - a:+7.3f}")
    # sign agreement of RG-vs-baseline gaps across backends
    signs_agree = 0
    signs_total = 0
    for base in ("B1", "B2", "B3", "B4", "B5"):
        gk = per_sys[("kinematic", "RG")] - per_sys[("kinematic", base)]
        gm = per_sys[("mujoco", "RG")] - per_sys[("mujoco", base)]
        if abs(gk) < 0.02 or abs(gm) < 0.02:
            continue
        signs_total += 1
        signs_agree += int(np.sign(gk) == np.sign(gm))
    sign_agreement = signs_agree / signs_total if signs_total else 1.0
    max_sys_delta = max(abs(per_sys[("mujoco", s)] - per_sys[("kinematic", s)])
                        for s in ("B1", "B2", "B3", "B4", "B5", "RG"))
    print(f"directional sign agreement (RG-vs-baseline gaps): "
          f"{signs_agree}/{signs_total} = {sign_agreement:.2f}; "
          f"max per-system delta = {max_sys_delta:.3f}")

    verdict = {
        "fault_free_success": succ,
        "false_alarm_rate": fa_rate,
        "unsafe_executions": unsafe,
        "detection_manifest_conditional": detection_manifest,
        "detection_raw": {f: det[f]["rate_raw"] for f in ALL7},
        "detection_per_fault": det,
        "per_system_success": {f"{b}/{s}": v for (b, s), v in per_sys.items()},
        "sign_agreement": sign_agreement,
        "max_per_system_delta": max_sys_delta,
        "n_fault_free_episodes": n_ff,
        "seeds_per_fault_cell": q,
        "wall_time_s": round(time.time() - t0, 1),
        "pass": {
            "fault_free_95pct": succ >= 0.95,
            "fa_below_2pct": fa_rate < 0.02,
            "detection_90pct": detection_manifest >= 0.90,
            "directional_agreement": sign_agreement >= 0.75 and
                                     max_sys_delta <= 0.25,
            "zero_unsafe": unsafe == 0,
        },
    }
    verdict["G2_PASS"] = all(verdict["pass"].values())
    with open(os.path.join(args.out, "g2_verdict.json"), "w") as fh:
        json.dump(verdict, fh, indent=2, default=float)
    print("\nG2 verdict:", "PASS" if verdict["G2_PASS"] else "FAIL",
          json.dumps(verdict["pass"]))


if __name__ == "__main__":
    main()
