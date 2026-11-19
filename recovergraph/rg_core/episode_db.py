"""Episode record database (spec 5.5): SQLite so offline analysis needs no ROS.

Schema (spec 5.5):
  episodes(id, seed, task_id, horizon, system, injected_fault_json,
           start_state, outcome, wall_time)
  events(episode_id, t, node, type, payload_json)
  ledger(...)            -- entries per 5.2
  diagnoses(episode_id, step, posterior_json, argmax, truth)
  recoveries(episode_id, step, candidates_json, chosen, cost_terms_json, result)
"""
from __future__ import annotations

import json
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY, seed INTEGER, task_id TEXT, horizon INTEGER,
  system TEXT, injected_fault_json TEXT, start_state TEXT, outcome TEXT,
  success INTEGER, wall_time REAL, metrics_json TEXT
);
CREATE TABLE IF NOT EXISTS events (
  episode_id TEXT, t REAL, node TEXT, type TEXT, payload_json TEXT
);
CREATE TABLE IF NOT EXISTS ledger (
  episode_id TEXT, step INTEGER, subgoal TEXT, skill TEXT,
  entry_json TEXT, divergence REAL, flagged INTEGER, fault_free INTEGER
);
CREATE TABLE IF NOT EXISTS diagnoses (
  episode_id TEXT, step INTEGER, posterior_json TEXT, argmax TEXT, truth TEXT
);
CREATE TABLE IF NOT EXISTS recoveries (
  episode_id TEXT, step INTEGER, candidates_json TEXT, chosen TEXT,
  cost_terms_json TEXT, result TEXT
);
"""


class EpisodeDB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()

    # ------------------------------------------------------------------ #
    def new_episode(self, ep_id: str, seed: int, task_id: str, horizon: int,
                    system: str, injected_fault: list, start_state: str) -> str:
        self.conn.execute(
            "INSERT OR REPLACE INTO episodes (id, seed, task_id, horizon, system,"
            " injected_fault_json, start_state) VALUES (?,?,?,?,?,?,?)",
            (ep_id, seed, task_id, horizon, system,
             json.dumps(injected_fault), start_state))
        self.conn.commit()
        return ep_id

    def finish_episode(self, ep_id: str, outcome: str, success: bool,
                       wall_time: float, metrics: dict) -> None:
        self.conn.execute(
            "UPDATE episodes SET outcome=?, success=?, wall_time=?, metrics_json=?"
            " WHERE id=?",
            (outcome, int(success), wall_time, json.dumps(metrics), ep_id))
        self.conn.commit()

    def log_event(self, ep_id: str, t: float, node: str, etype: str,
                  payload: dict) -> None:
        self.conn.execute(
            "INSERT INTO events VALUES (?,?,?,?,?)",
            (ep_id, t, node, etype, json.dumps(payload)))
        self.conn.commit()

    def log_ledger(self, entry) -> None:
        d = entry.to_dict()
        self.conn.execute(
            "INSERT INTO ledger VALUES (?,?,?,?,?,?,?,?)",
            (entry.episode_id, entry.step, entry.subgoal, entry.skill,
             entry.to_json(), entry.divergence_D, int(entry.divergence_flagged),
             int(entry.fault_free)))
        self.conn.commit()

    def log_diagnosis(self, ep_id: str, step: int, diagnosis, truth: str) -> None:
        self.conn.execute(
            "INSERT INTO diagnoses VALUES (?,?,?,?,?)",
            (ep_id, step, json.dumps(diagnosis.to_dict()), diagnosis.argmax, truth))
        self.conn.commit()

    def log_recovery(self, ep_id: str, step: int, decision, result: str) -> None:
        self.conn.execute(
            "INSERT INTO recoveries VALUES (?,?,?,?,?,?)",
            (ep_id, step, json.dumps(decision.to_dict()), decision.chosen,
             json.dumps(decision.cost_terms), result))
        self.conn.commit()

    # ------------------------------------------------------------------ #
    def episode_rows(self, system: str | None = None) -> list:
        if system:
            cur = self.conn.execute(
                "SELECT * FROM episodes WHERE system=? ORDER BY id", (system,))
        else:
            cur = self.conn.execute("SELECT * FROM episodes ORDER BY id")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def counts(self) -> dict:
        out = {}
        for table in ("episodes", "events", "ledger", "diagnoses", "recoveries"):
            out[table] = self.conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return out
