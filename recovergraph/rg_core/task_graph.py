"""Task graph with carried-variable dependency edges (spec 5.1, 6.5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from .types import pred_parts


@dataclass
class Subgoal:
    id: str
    skill: str                      # pick | place | move
    args: dict
    pre: list                       # predicate strings
    eff: list                       # predicate strings


class TaskGraph:
    """DAG of subgoals; each edge records which state variable it carries
    (e.g. g1 -> g2 carries holding(red_cube)), enabling precise invalidation."""

    def __init__(self, task_id: str, goal: str, subgoals: list,
                 depends_on: list, invariants: list):
        self.task_id = task_id
        self.goal = goal
        self.subgoals: dict = {s.id: s for s in subgoals}
        self.invariants = list(invariants)
        self.g = nx.DiGraph()
        for s in subgoals:
            self.g.add_node(s.id, subgoal=s)
        for u, v, carried in depends_on:
            if u not in self.subgoals or v not in self.subgoals:
                raise ValueError(f"edge ({u},{v}) references unknown subgoal")
            self.g.add_edge(u, v, carried=list(carried))
        if not nx.is_directed_acyclic_graph(self.g):
            raise ValueError("depends_on must form a DAG (spec 5.1)")

    # ------------------------------------------------------------------ #
    def topo_order(self) -> list:
        return list(nx.topological_sort(self.g))

    def subgoal(self, sid: str) -> Subgoal:
        return self.subgoals[sid]

    def downstream_of(self, sid: str) -> list:
        """All transitive successors of a subgoal (ledger 'downstream' field)."""
        return list(nx.descendants(self.g, sid))

    def horizon(self) -> int:
        return len(self.subgoals)

    # ------------------------------------------------------------------ #
    # Dependency-aware invalidation (spec 6.5)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _effect_vars(sub: Subgoal) -> list:
        """Predicates produced by a subgoal (its effect variables)."""
        return list(sub.eff)

    def mark_stale(self, corrupted_vars: list) -> tuple:
        """Traverse descendants through edges that carry corrupted variables;
        stale subgoals must re-verify preconditions before execution.
        When a subgoal is marked stale, its own effect variables become
        uncertain too and propagation continues (conservative fixpoint).

        Returns (stale_set, corrupted_vars_final)."""
        corrupted = set(corrupted_vars)
        stale = set()
        changed = True
        while changed:
            changed = False
            for u, v, data in self.g.edges(data=True):
                carried = data.get("carried") or self._effect_vars(self.subgoals[u])
                if (u in stale or any(c in corrupted for c in carried)) and v not in stale:
                    stale.add(v)
                    corrupted.update(self._effect_vars(self.subgoals[v]))
                    changed = True
        return stale, corrupted

    def check_invariants(self, true_facts: set) -> list:
        """Invariants are predicates that must hold until the end (spec 5.1).
        true_facts: set of predicate strings currently true in the world."""
        violated = []
        for inv in self.invariants:
            head, args = pred_parts(inv)
            fact = inv.split(" must")[0].strip()
            if fact not in true_facts:
                violated.append(inv)
        return violated

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "subgoals": [
                {"id": s.id, "skill": s.skill, "args": s.args,
                 "pre": s.pre, "eff": s.eff}
                for s in self.subgoals.values()
            ],
            "depends_on": [
                [u, v, list(d.get("carried", []))]
                for u, v, d in self.g.edges(data=True)
            ],
            "invariants": list(self.invariants),
        }

    @staticmethod
    def from_dict(d: dict) -> "TaskGraph":
        subs = [Subgoal(s["id"], s["skill"], s["args"], s["pre"], s["eff"])
                for s in d["subgoals"]]
        return TaskGraph(d["task_id"], d["goal"], subs,
                         [tuple(e) for e in d["depends_on"]], d["invariants"])

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict())

    @staticmethod
    def from_json(s: str) -> "TaskGraph":
        import json
        return TaskGraph.from_dict(json.loads(s))
