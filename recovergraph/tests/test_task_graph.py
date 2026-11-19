"""Task graph, dependency invalidation (spec 6.5) and task generator tests."""
import pytest

from rg_core.task_graph import Subgoal, TaskGraph
from rg_core.taskgen import make_task
from rg_core.types import holding_pred, in_pred, on_pred


def build_chain() -> TaskGraph:
    g1 = Subgoal("g1", "pick", {"obj": "red_cube"},
                 ["reachable(red_cube)", "gripper_empty"], [holding_pred("red_cube")])
    g2 = Subgoal("g2", "place", {"obj": "red_cube", "target": "bin"},
                 [holding_pred("red_cube")],
                 [in_pred("red_cube", "bin"), "gripper_empty"])
    g3 = Subgoal("g3", "pick", {"obj": "blue_cube"},
                 ["gripper_empty"], [holding_pred("blue_cube")])
    return TaskGraph(
        "t_0012", "put red cube in bin, then pick blue cube", [g1, g2, g3],
        [("g1", "g2", [holding_pred("red_cube")]),
         ("g2", "g3", ["gripper_empty"])],
        [f"{in_pred('red_cube', 'bin')} must hold until end"],
    )


class TestTaskGraph:
    def test_topological_order(self):
        assert build_chain().topo_order() == ["g1", "g2", "g3"]

    def test_downstream(self):
        assert set(build_chain().downstream_of("g1")) == {"g2", "g3"}

    def test_cycle_rejected(self):
        g1 = Subgoal("g1", "pick", {"obj": "x"}, [], ["holding(x)"])
        g2 = Subgoal("g2", "place", {"obj": "x"}, ["holding(x)"], [])
        with pytest.raises(ValueError, match="DAG"):
            TaskGraph("t", "g", [g1, g2], [("g1", "g2", []), ("g2", "g1", [])], [])

    def test_invalidation_through_carried_vars(self):
        tg = build_chain()
        stale, corrupted = tg.mark_stale([holding_pred("red_cube")])
        # g2 stale (carried holding(red_cube)), g3 stale transitively
        # (g2's own effects became uncertain); g1 is retained.
        assert stale == {"g2", "g3"}
        assert "g1" not in stale
        assert in_pred("red_cube", "bin") in corrupted
        assert "gripper_empty" in corrupted
        assert holding_pred("blue_cube") in corrupted

    def test_invalidation_only_through_corrupted_edge(self):
        tg = build_chain()
        stale, _ = tg.mark_stale([holding_pred("blue_cube")])
        # No edge carries holding(blue_cube): nothing downstream to invalidate.
        assert stale == set()

    def test_non_stale_subgoals_retained(self):
        """Spec 6.5 step 3: non-stale subgoals are retained - the key
        difference from full replanning."""
        tg = build_chain()
        stale, _ = tg.mark_stale(["gripper_empty"])
        assert stale == {"g3"}
        assert "g1" in tg.subgoals and "g2" in tg.subgoals

    def test_invariant_check(self):
        tg = build_chain()
        assert tg.check_invariants({in_pred("red_cube", "bin")}) == []
        violated = tg.check_invariants(set())
        assert violated and violated[0].startswith("in(red_cube,bin)")

    def test_json_roundtrip(self):
        tg = build_chain()
        tg2 = TaskGraph.from_json(tg.to_json())
        assert tg2.topo_order() == tg.topo_order()
        assert tg2.invariants == tg.invariants


class TestTaskGen:
    @pytest.mark.parametrize("h", [1, 2, 3, 5, 7, 10])
    @pytest.mark.parametrize("tpl", range(20))
    def test_horizon_and_template_validity(self, h, tpl):
        t = make_task(h, tpl, seed=0)
        assert t.horizon() == h
        assert len(set(t.subgoals)) == h
        assert t.topo_order() == sorted(t.subgoals.keys(),
                                        key=lambda s: int(s[1:]))
        assert t.goal
        # every edge carries state variables that exist in the task's effect
        # vocabulary (carried vars may flow through intermediate nodes,
        # e.g. pick -> move -> place all carry holding(obj) - spec 5.1)
        all_effs = set().union(*[set(s.eff) for s in t.subgoals.values()])
        for u, v, carried in t.g.edges(data=True):
            assert set(carried["carried"]) <= all_effs

    def test_deterministic(self):
        assert make_task(3, 0, 0).to_json() == make_task(3, 0, 0).to_json()

    def test_templates_differ(self):
        a = make_task(3, 0, 0).goal
        b = make_task(3, 1, 0).goal
        assert a != b

    def test_invalid_horizon(self):
        with pytest.raises(ValueError):
            make_task(0)
        with pytest.raises(ValueError):
            make_task(11)
