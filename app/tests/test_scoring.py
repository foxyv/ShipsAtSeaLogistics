import unittest

from saslogi.model import TICKS_PER_HOUR, parse_snapshot
from saslogi.planner import Capacity, make_plan
from saslogi.scoring import STOP_HOURS, Weights, plan_terms, scale_of
from tests.test_planner import mission, store

NOW = 5_000 * TICKS_PER_HOUR


def world(missions, containers, game_speed=8.0):
    """Ports A, B and C in a line, 10 km apart, and the game clock at NOW."""
    ports = [{"id": f"Port_{name}", "name": name, "location": [x * 100_000.0, 0.0, 0.0]}
             for name, x in (("A", 0), ("B", 10), ("C", 20))]
    return parse_snapshot({"time": "t", "ports": ports, "missions": missions, "containers": containers,
                           "calendar": {"nowTicks": str(NOW), "gameSpeed": game_speed}})


def with_deadline(raw, hours):
    raw["deadlineTicks"] = str(NOW + int(hours * TICKS_PER_HOUR))
    return raw


def order_of(plan):
    return [stop.port for stop in plan.stops]


class ScoringTests(unittest.TestCase):
    def test_sailing_time_follows_the_clock_speed(self):
        snap = world([], [])
        slow = snap.sail_hours(10)
        snap.game_speed = 16.0
        self.assertAlmostEqual(snap.sail_hours(10), 2 * slow)

    def test_an_urgent_contract_goes_first_even_when_it_is_further(self):
        # B is 10 km away and C 20 km. Taking B first would reach C too late for the urgent contract.
        reach_c_first = world([], []).sail_hours(20) + STOP_HOURS
        reach_c_second = world([], []).sail_hours(20) + 2 * STOP_HOURS
        deadline = (reach_c_first + reach_c_second) / 2
        snap = world([with_deadline(mission("urgent", "C", ["g1"]), deadline), mission("plain", "B", ["g2"])],
                     [store("warehouse", "A", ["g1", "g2"])])
        plan = make_plan(snap, "Port_A", Capacity(), "custom", max_stops=6)
        self.assertEqual(order_of(plan)[:2], ["Port_A", "Port_C"])
        terms = plan_terms(snap, plan, scale_of(snap), 0.0)
        self.assertEqual(terms["late"], 0.0)
        self.assertEqual(plan.delivered, 2)

    def test_a_contract_that_cannot_arrive_in_time_is_not_chased(self):
        snap = world([with_deadline(mission("urgent", "C", ["g1"], money=400), 1.0)],
                     [store("warehouse", "A", ["g1"])])
        plan = make_plan(snap, "Port_A", Capacity(), "custom", max_stops=6)
        self.assertEqual(plan.total_money, 0)  # it would arrive late and earn nothing

    def test_a_late_arrival_counts_as_late_and_earns_nothing(self):
        from saslogi.planner import Plan, Stop
        snap = world([with_deadline(mission("urgent", "C", ["g1"], money=400), 1.0)],
                     [store("warehouse", "A", ["g1"])])
        item = snap.items[0]
        plan = Plan(stops=[Stop(port="Port_A", leg_km=0.0, load=[item], onboard_count=1),
                           Stop(port="Port_C", leg_km=20.0, unload=[item], onboard_count=0, earned=0.0)])
        terms = plan_terms(snap, plan, scale_of(snap), 0.0)
        self.assertEqual(terms["late"], 1.0)
        self.assertEqual(terms["contracts"], 0.0)

    def test_urgent_cargo_is_loaded_first_when_the_boat_is_full(self):
        snap = world([with_deadline(mission("urgent", "B", ["g1"], money=10), 200.0),
                      mission("rich", "B", ["g2"], money=1000)],
                     [store("warehouse", "A", ["g1", "g2"])])
        plan = make_plan(snap, "Port_A", Capacity(max_items=1), "full", max_stops=6)
        self.assertEqual([i.guid for i in plan.stops[0].load], ["g1"])

    def test_empty_legs_are_the_share_of_distance_sailed_with_nothing_aboard(self):
        from saslogi.planner import Plan, Stop
        snap = world([mission("m1", "B", ["g1"]), mission("m2", "A", ["g2"])],
                     [store("warehouse", "A", ["g1"]), store("warehouse", "C", ["g2"])])
        a, b, c = snap.items[0], snap.items[0], snap.items[1]
        plan = Plan(stops=[Stop(port="Port_A", leg_km=0.0, load=[a], onboard_count=1),
                           Stop(port="Port_B", leg_km=10.0, unload=[b], onboard_count=0),
                           Stop(port="Port_C", leg_km=10.0, load=[c], onboard_count=1)])
        terms = plan_terms(snap, plan, scale_of(snap), 0.0)
        self.assertAlmostEqual(terms["empty"], 0.5)  # the leg from B to C had nothing aboard

    def test_the_planner_avoids_an_empty_leg_when_it_can(self):
        snap = world([mission("m1", "B", ["g1"]), mission("m2", "C", ["g2"])],
                     [store("warehouse", "A", ["g1"]), store("warehouse", "C", ["g2"])])
        plan = make_plan(snap, "Port_A", Capacity(), "custom", max_stops=6)
        self.assertEqual(plan_terms(snap, plan, scale_of(snap), 0.0)["empty"], 0.0)

    def test_stranded_cargo_is_counted(self):
        snap = world([mission("m1", "B", ["g1"]), mission("m2", "C", ["g2"])],
                     [store("warehouse", "A", ["g1", "g2"])])
        short = make_plan(snap, "Port_A", Capacity(), "custom", max_stops=2)
        full = make_plan(snap, "Port_A", Capacity(), "custom", max_stops=6)
        scale = scale_of(snap)
        self.assertGreater(plan_terms(snap, short, scale, 0.0)["stranded"], plan_terms(snap, full, scale, 0.0)["stranded"])

    def test_the_default_weights_deliver_both_contracts(self):
        snap = world([mission("far", "C", ["g1"], money=900), mission("near", "B", ["g2"], money=100)],
                     [store("warehouse", "A", ["g1", "g2"])])
        plan = make_plan(snap, "Port_A", Capacity(), "custom", max_stops=6, weights=Weights())
        self.assertEqual(plan.delivered, 2)


if __name__ == "__main__":
    unittest.main()
