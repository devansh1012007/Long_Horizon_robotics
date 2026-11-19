"""Learning loop (spec Phase 4b, M5): episode database -> learned tables.

Fits, from accumulated episodes (calibration split only - never scored):
  - likelihood tables P(evidence feature | fault)          -> A5 / RG-LEARNED
  - skill-conditioned priors P(fault | skill)              -> RG-LEARNED
  - affordance failure tables P(fail | recovery, fault)    -> RG-LEARNED
  - forward-model parameters (p_grasp, p_slip)             -> sensitivity
Every fitted cell carries a count; cells below `min_count` fall back to the
hand-set spec table and the fallback is recorded in the artifact (honest
reporting). The artifact is versioned JSON; A5/RG-LEARNED load it lazily and
fall back to the hand-set tables when it is absent.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict

from .diagnosis import LIKELIHOODS, SKILL_PRIORS
from .recovery import PFAIL
from .types import FAULTS

DIAGNOSABLE = [f for f in FAULTS if f != "f_none"]

EVIDENCE_FEATURES = list(LIKELIHOODS.keys())
SKILLS = ["pick", "move", "place"]
RECOVERIES = [r for r in PFAIL if r != "escalate"]


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #
def _rows(db_path: str, table: str) -> list:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def load_episodes(dbs: list) -> dict:
    """Load episodes/ledger/diagnoses/recoveries/events from one or more DBs."""
    out = {"episodes": [], "ledger": [], "diagnoses": [], "recoveries": [],
           "events": []}
    for db in dbs:
        for table in out:
            try:
                out[table] += _rows(db, table)
            except sqlite3.Error:
                pass               # table missing in this db
    return out


# --------------------------------------------------------------------------- #
# Table fitting
# --------------------------------------------------------------------------- #
def fit_likelihoods(data: dict, min_count: int = 15,
                    smoothing: float = 1.0) -> tuple:
    """P(feature observed | fault) over ALL fault-active ledger steps
    (detected or not): each fault-active step's evidence is joined to the
    injected fault via the ledger's plan_index. Fitting on dispatched-only
    rows would be selection-biased (the distinguishing-but-undetected
    features, e.g. bystander displacement under f_collision, would never
    enter the fit). Per-fault fallback to the hand-set column when the fault
    has fewer than min_count fault-active rows."""
    sched = defaultdict(dict)          # episode_id -> plan_index -> label
    for e in data["episodes"]:
        for ev in json.loads(e.get("injected_fault_json") or "[]"):
            sched[e["id"]][int(ev.get("step", -1))] = ev.get("label", ev["fault"])
    totals = defaultdict(int)
    counts = defaultdict(lambda: defaultdict(int))
    for r in data["ledger"]:
        if r.get("fault_free", 1):
            continue
        truth = sched.get(r["episode_id"], {}).get(
            int(json.loads(r.get("entry_json") or "{}").get(
                "plan_index", -1)), None)
        if truth not in DIAGNOSABLE:
            continue
        ev = json.loads(r.get("entry_json") or "{}").get("evidence", {})
        if not ev:
            continue
        totals[truth] += 1
        for feat in EVIDENCE_FEATURES:
            if ev.get(feat):
                counts[feat][truth] += 1

    table, fallbacks = {}, {}
    for feat in EVIDENCE_FEATURES:
        table[feat] = {}
        for fault in FAULTS:
            if fault == "f_none" or totals.get(fault, 0) < min_count:
                fallbacks[fault] = True
                table[feat][fault] = LIKELIHOODS[feat][fault]
                continue
            n = totals[fault]
            k = counts[feat][fault]
            p = (k + smoothing) / (n + 2 * smoothing)
            table[feat][fault] = round(min(0.99, max(0.01, p)), 4)
    # re-normalise f_none columns to the hand-set prior column
    for feat in EVIDENCE_FEATURES:
        table[feat]["f_none"] = LIKELIHOODS[feat]["f_none"]
    return table, {"min_count": min_count,
                   "fault_rows": dict(totals),
                   "fallback_faults": sorted(fallbacks)}


def fit_skill_priors(data: dict, pseudo: float = 5.0) -> tuple:
    """P(fault | skill) with the hand-set table as the conjugate prior
    (pseudo-counts). Counted per EPISODE-fault (a fault episode logs many
    diagnosis rows through its retry loop; counting rows would over-weight
    always-manifesting faults like f_controller)."""
    led = {(r["episode_id"], r["step"]): r for r in data["ledger"]}
    counts = defaultdict(lambda: defaultdict(int))
    seen = set()
    for d in data["diagnoses"]:
        truth = d["truth"]
        if truth not in DIAGNOSABLE:
            continue
        key = (d["episode_id"], truth)
        if key in seen:
            continue                 # one count per episode-fault
        seen.add(key)
        entry = led.get((d["episode_id"], d["step"]))
        if entry is None:
            continue
        counts[entry["skill"]][truth] += 1
    table = {}
    for skill in SKILLS:
        table[skill] = {}
        z = 0.0
        for fault in FAULTS:
            base = SKILL_PRIORS[skill][fault]
            p = (counts[skill][fault] + pseudo * base) / \
                (sum(counts[skill].values()) + pseudo)
            table[skill][fault] = p
            z += p
        for fault in FAULTS:
            table[skill][fault] = round(table[skill][fault] / z, 4)
    return table, {"pseudo_counts": pseudo,
                   "counts": {s: dict(c) for s, c in counts.items()}}


def fit_pfail(data: dict, min_count: int = 10,
              smoothing: float = 1.0) -> tuple:
    """P(fail | recovery, fault) from recovery outcomes joined to the
    diagnosis argmax at the same step. Failed attempts are logged with
    result='failed'; verified recoveries with result='verified'."""
    diag = {(d["episode_id"], d["step"]): d for d in data["diagnoses"]}
    counts = defaultdict(lambda: defaultdict(int))
    fails = defaultdict(lambda: defaultdict(int))
    for r in data["recoveries"]:
        d = diag.get((r["episode_id"], r["step"]))
        fault = d["argmax"] if d else "f_none"
        chosen = r["chosen"]
        if chosen == "verify" or chosen == "escalate":
            continue
        counts[chosen][fault] += 1
        if r["result"] != "verified":
            fails[chosen][fault] += 1
    table, fallbacks = {}, {}
    for r in RECOVERIES:
        table[r] = {}
        for fault in FAULTS:
            n = counts[r][fault]
            if n < min_count:
                fallbacks[r] = True
                table[r][fault] = PFAIL[r][fault]
                continue
            p = (fails[r][fault] + smoothing) / (n + 2 * smoothing)
            table[r][fault] = round(min(0.99, max(0.01, p)), 4)
    return table, {"min_count": min_count,
                   "counts": {r: dict(c) for r, c in counts.items()},
                   "recoveries_with_sparse_cells": sorted(fallbacks)}


def fit_forward_params(data: dict) -> dict:
    """Empirical p_grasp / p_slip from fault-free episodes' execution events
    (grasp_closed vs gripper_closed_narrow; slip_drop per pick-scale
    transport approximation, documented)."""
    fault_free_eps = {e["id"] for e in data["episodes"]
                      if not json.loads(e.get("injected_fault_json") or "[]")}
    picks = grasp_ok = slips = 0
    for ev in data["events"]:
        if ev["episode_id"] not in fault_free_eps or ev["type"] not in \
                ("exec", "recovery_pick"):
            continue
        payload = json.loads(ev.get("payload_json") or "{}")
        events = payload.get("events", [])
        if ev["type"] == "recovery_pick" or "grasp_closed" in events or \
                "gripper_closed_narrow" in events:
            picks += 1
            ok = ("grasp_closed" in events) or bool(payload.get("success"))
            grasp_ok += int(ok)
        slips += sum(1 for e in events if e == "slip_drop")
    p_grasp = round(grasp_ok / picks, 4) if picks else None
    # transport count approximated by pick count (each successful pick is
    # transported once); slips are rare so the estimate is coarse.
    p_slip = round(slips / picks, 5) if picks and slips else None
    return {"p_grasp": p_grasp, "p_slip": p_slip,
            "n_pick_events": picks, "n_slip_events": slips}


# --------------------------------------------------------------------------- #
# Artifact assembly
# --------------------------------------------------------------------------- #
def build_artifact(dbs: list, min_count: int = 15,
                   prior_dbs: list | None = None) -> dict:
    """dbs: all calibration DBs (likelihoods/affordances need balanced
    coverage). prior_dbs: natural-rate episodes only (priors need deployment
    base rates, so pinned fault cells are excluded); defaults to dbs."""
    data = load_episodes(dbs)
    prior_data = load_episodes(prior_dbs) if prior_dbs else data
    likelihoods, lh_meta = fit_likelihoods(data, min_count=min_count)
    priors, pr_meta = fit_skill_priors(prior_data)
    pfail, pf_meta = fit_pfail(data)
    forward = fit_forward_params(data)
    # sensitivity vs the hand-set tables (spec risk table requirement)
    deltas = [abs(likelihoods[f][fault] - LIKELIHOODS[f][fault])
              for f in EVIDENCE_FEATURES for fault in FAULTS]
    return {
        "version": "learned_v1",
        "fitted_on_dbs": [os.path.abspath(p) for p in dbs],
        "prior_dbs": [os.path.abspath(p) for p in (prior_dbs or dbs)],
        "n_rows": {k: len(v) for k, v in data.items()},
        "likelihoods": likelihoods,
        "skill_priors": priors,
        "pfail": pfail,
        "forward_params": forward,
        "fit_meta": {"likelihoods": lh_meta, "priors": pr_meta,
                     "pfail": pf_meta},
        "sensitivity_vs_handset": {
            "max_abs_delta": round(max(deltas), 4),
            "mean_abs_delta": round(sum(deltas) / max(1, len(deltas)), 5),
        },
    }


def save_artifact(artifact: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(artifact, fh, indent=2)
    return path
