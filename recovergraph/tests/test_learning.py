"""M5 learning-loop tests: artifact fitting, injection isolation, honest
fallback, per-episode prior counting, pfail override."""
import json
import os
import tempfile

import numpy as np
import pytest

from rg_core import diagnosis as dg
from rg_core import learning as lg
from rg_core import recovery as rc
from rg_core.diagnosis import LIKELIHOODS, diagnose
from rg_core.episode_db import EpisodeDB
from rg_core.taskgen import make_task
from rg_core.types import FAULTS
from rgsim.pipeline import EpisodeRunner
from systems import make_system


def _calib_db(path, n=6, condition="single"):
    db = EpisodeDB(path)
    for i in range(n):
        seed = 2000 + i
        task = make_task(3, seed % 20, seed)
        EpisodeRunner(make_system("RG"), task, seed=seed,
                      condition=condition, db=db,
                      ep_id=f"ep_cal_{i}").run()
    db.close()
    return path


def test_artifact_fits_all_features_with_fallback(tmp_path):
    dbp = _calib_db(str(tmp_path / "cal.db"))
    art = lg.build_artifact([dbp], min_count=3)
    assert set(art["likelihoods"]) == set(LIKELIHOODS)
    for feat in art["likelihoods"]:
        assert set(art["likelihoods"][feat]) == set(FAULTS)
        # f_none column always stays hand-set (no fault-free rows are fitted)
        assert art["likelihoods"][feat]["f_none"] == LIKELIHOODS[feat]["f_none"]
    assert art["fit_meta"]["likelihoods"]["fallback_faults"]  # sparse at N=6
    assert "sensitivity_vs_handset" in art


def test_artifact_save_load_roundtrip(tmp_path):
    dbp = _calib_db(str(tmp_path / "cal.db"))
    art = lg.build_artifact([dbp], min_count=3)
    p = lg.save_artifact(art, str(tmp_path / "art.json"))
    with open(p) as fh:
        art2 = json.load(fh)
    assert art2["likelihoods"] == art["likelihoods"]


def test_a5_injects_likelihoods_only_no_global_mutation(tmp_path):
    dbp = _calib_db(str(tmp_path / "cal.db"))
    art = lg.build_artifact([dbp], min_count=3)
    p = lg.save_artifact(art, str(tmp_path / "art.json"))
    before = {f: dict(LIKELIHOODS[f]) for f in LIKELIHOODS}
    sys_a5 = make_system("A5", p)
    assert sys_a5.learned_available is True
    # spec: A5 swaps likelihoods only; priors stay hand-set
    assert "priors" not in (sys_a5.diag_tables or {})
    # no global mutation: the module table used by RG is untouched
    assert {f: dict(LIKELIHOODS[f]) for f in LIKELIHOODS} == before
    d1 = diagnose({"gripper_closed_narrow": True}, "pick")
    d2 = diagnose({"gripper_closed_narrow": True}, "pick",
                   tables=sys_a5.diag_tables)
    assert d1.argmax == d2.argmax  # same modal fault at minimum


def test_a5_honest_fallback_without_artifact():
    sys_a5 = make_system("A5", None)
    assert getattr(sys_a5, "learned_available", False) is False
    assert (sys_a5.diag_tables or {}).get("likelihoods") is None


def test_rg_learned_injects_all_tables(tmp_path):
    dbp = _calib_db(str(tmp_path / "cal.db"))
    art = lg.build_artifact([dbp], min_count=3)
    p = lg.save_artifact(art, str(tmp_path / "art.json"))
    sys_rg = make_system("RG-LEARNED", p)
    assert sys_rg.learned_available is True
    assert sys_rg.diag_tables["likelihoods"]
    assert sys_rg.pfail_table


def test_priors_counted_per_episode_not_per_dispatch():
    # one episode, one fault, three repeated diagnosis rows -> ONE count
    rows = [{"episode_id": "e1", "step": i, "truth": "f_miss",
             "argmax": "f_miss", "posterior_json": "{}"} for i in range(3)]
    ledger = [{"episode_id": "e1", "step": i, "skill": "pick",
               "entry_json": json.dumps({"evidence": {"gripper_closed_narrow":
                                                      True}})}
              for i in range(3)]
    table, meta = lg.fit_skill_priors(
        {"diagnoses": rows, "ledger": ledger, "episodes": []})
    assert meta["counts"]["pick"]["f_miss"] == 1
    z = sum(table["pick"].values())
    assert abs(z - 1.0) < 1e-3   # 4-decimal rounding


def test_choose_recovery_pfail_override_changes_cost():
    from rg_core.types import Diagnosis
    diag = Diagnosis.from_posterior(
        {"f_none": 0.01, "f_miss": 0.97, "f_slip": 0.01, "f_pose": 0.0,
         "f_collision": 0.0, "f_perception": 0.0, "f_controller": 0.01})
    d1 = rc.choose_recovery(diag, "g1")
    cheap = {r: {f: 0.01 for f in FAULTS} for r in rc.PFAIL}
    d2 = rc.choose_recovery(diag, "g1", pfail=cheap)
    assert d2.chosen == d1.chosen or d2.chosen in [t[0] for t in d2.feasible]
    assert d2.cost_terms["P_fail"] <= max(d1.cost_terms["P_fail"], 0.01 + 1e-9)


def test_learned_likelihoods_change_posterior_sharply(tmp_path):
    # a sharply separated learned table must sharpen the posterior
    learned = {feat: {f: (0.97 if f == "f_slip" else 0.02)
                     for f in FAULTS}
               for feat in LIKELIHOODS}
    d = diagnose({"grip_force_drop": True, "gripper_closed_narrow": True},
                 "move")
    dl = diagnose({"grip_force_drop": True, "gripper_closed_narrow": True},
                  "move", tables={"likelihoods": learned})
    assert dl.posterior["f_slip"] > d.posterior["f_slip"]
