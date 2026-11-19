"""Episode DB (spec 5.5) and JSON round-trip tests."""
import numpy as np
import pytest

from rg_core.diagnosis import diagnose
from rg_core.episode_db import EpisodeDB
from rg_core.types import BeliefState, LedgerEntry, RecoveryDecision


def entry():
    return LedgerEntry(
        episode_id="ep_000341", step=4, subgoal="g2", skill="place",
        preconditions={"holding(red_cube)": {"predicted": True, "conf": 0.93}},
        expected_effects={"pose(red_cube)":
                          {"mean": [0.42, 0.10, 0.04, 0], "cov_diag": [1e-4] * 3 + [1e-3]}},
        predicted_state_conf=0.88,
        observed_state={"pose(red_cube)":
                        {"mean": [0.47, 0.10, 0.04, 0], "cov_diag": [4e-4] * 3 + [2e-3]}},
        actual_effects=["pose_error=0.05m"], divergence_D=18.7,
        divergence_flagged=True, downstream=["g3"], timestamp=1731800000.12,
        fault_free=False,
    )


class TestJsonRoundtrip:
    def test_ledger_entry(self):
        e = entry()
        e2 = LedgerEntry.from_json(e.to_json())
        assert e2 == e

    def test_belief_state(self):
        b = BeliefState(
            t=1.5, ee_pose=np.array([0.3, 0.1, 0.2, 0.4]),
            ee_cov_diag=np.full(4, 1e-4),
            gripper=__import__("rg_core.types", fromlist=["GripperState"])
            .GripperState("holding", 0.05, 25.0, 25.0),
            objects={"red_cube": __import__("rg_core.types", fromlist=["ObjectEst"])
                     .ObjectEst("red_cube", np.array([0.3, 0.1, 0.02, 0.0]),
                                np.full(4, 4e-4))},
            confidence={"holding(red_cube)": 0.9},
        )
        b2 = BeliefState.from_json(b.to_json())
        assert np.allclose(b2.ee_pose, b.ee_pose)
        assert b2.gripper.state == "holding"
        assert "red_cube" in b2.objects

    def test_recovery_decision(self):
        d = RecoveryDecision(chosen="regrasp", params={"obj": "red_cube"},
                             cost_terms={"cost": 18.0})
        assert RecoveryDecision.from_dict(d.to_dict()) == d


class TestEpisodeDB:
    def test_full_roundtrip(self, tmp_path):
        db = EpisodeDB(str(tmp_path / "ep.db"))
        db.new_episode("ep_1", seed=42, task_id="t_003_00", horizon=3,
                       system="RG", injected_fault=[{"fault": "f_slip"}],
                       start_state="{}")
        db.log_event("ep_1", 0.0, "skill_executor", "exec_start", {"subgoal": "g1"})
        e = entry()
        e.episode_id = "ep_1"
        db.log_ledger(e)
        d = diagnose({"grip_force_drop": True}, "move")
        db.log_diagnosis("ep_1", 4, d, truth="f_slip")
        dec = RecoveryDecision(chosen="regrasp", cost_terms={"cost": 18.0})
        db.log_recovery("ep_1", 4, dec, result="restored")
        db.finish_episode("ep_1", "success", True, 61.2,
                          {"extra_actions": 3})
        c = db.counts()
        assert c == {"episodes": 1, "events": 1, "ledger": 1,
                     "diagnoses": 1, "recoveries": 1}
        rows = db.episode_rows("RG")
        assert rows[0]["id"] == "ep_1" and rows[0]["success"] == 1
        assert rows[0]["metrics_json"] and "extra_actions" in rows[0]["metrics_json"]
        assert db.episode_rows("B2") == []
        db.close()
