"""Cargo that comes aboard after the route was planned (loaded by hand, or by the crane operator under an older
plan) has no line in the plan. What is for the port the boat is in is added to that stop's unloads at once; the
rest makes the planner plan again."""

from saslogi.planner import Capacity, Plan, Stop, make_plan, unplanned_aboard, with_unplanned_unloads

from tests.test_planner import mission, store
from tests.test_scoring import world


def two_pieces_aboard():
    """m1 (g1) for B and m2 (g2) for B, both on the boat; and m3 (g3) for C, also aboard."""
    return world([mission("m1", "B", ["g1"]), mission("m2", "B", ["g2"]), mission("m3", "C", ["g3"])],
                 [store("boatCargoArea", None, ["g1", "g2", "g3"])])


def plan_that_knows_only_g1(snap):
    items = {i.guid: i for i in snap.items}
    return Plan(stops=[Stop(port="Port_B", unload=[items["g1"]]), Stop(port="Port_C")])


def test_cargo_the_plan_does_not_mention_is_found():
    snap = two_pieces_aboard()
    assert sorted(i.guid for i in unplanned_aboard(plan_that_knows_only_g1(snap), snap)) == ["g2", "g3"]


def test_cargo_for_the_port_the_boat_is_in_is_added_to_that_stops_unloads():
    snap = two_pieces_aboard()
    plan = with_unplanned_unloads(plan_that_knows_only_g1(snap), snap, "Port_B")
    assert sorted(i.guid for i in plan.stops[0].unload) == ["g1", "g2"]  # g3 is for C, so it waits for a new route
    assert plan.stops[1].unload == []


def test_nothing_is_added_when_the_boat_is_not_at_the_plans_first_stop():
    snap = two_pieces_aboard()
    plan = plan_that_knows_only_g1(snap)
    assert with_unplanned_unloads(plan, snap, "Port_C") is plan
    assert with_unplanned_unloads(plan, snap, None) is plan


def test_cargo_already_in_the_plan_is_not_added_twice():
    snap = two_pieces_aboard()
    once = with_unplanned_unloads(plan_that_knows_only_g1(snap), snap, "Port_B")
    twice = with_unplanned_unloads(once, snap, "Port_B")
    assert twice is once


def test_a_freshly_made_plan_leaves_no_cargo_unmentioned():
    snap = two_pieces_aboard()
    plan = make_plan(snap, "Port_A", Capacity())
    assert unplanned_aboard(plan, snap) == []
