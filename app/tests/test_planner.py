import unittest

from saslogi.model import AT_SEA, BOAT, parse_snapshot
from saslogi.packing import Hold
from saslogi.planner import (
    Capacity, advance, check_load, completing_unloads, has_work, make_plan, remaining, waiting_by_contract,
)


def snapshot(missions, containers):
    ports = [{"id": f"Port_{name}", "name": name, "location": [x * 100_000.0, 0.0, 0.0]}
             for name, x in (("A", 0), ("B", 10), ("C", 20))]
    return parse_snapshot({"time": "t", "ports": ports, "missions": missions, "containers": containers})


def mission(mid, end, guids, delivered=(), money=100):
    return {
        "title": mid, "status": "InProgress",
        "context": {"Mission.Descriptor.MissionID": mid, "Mission.Descriptor.Start": "Port_A",
                    "Mission.Descriptor.End": f"Port_{end}", "Mission.Data.Spawned": list(guids),
                    "Mission.Rewards.Delivery.DeliverededReward": list(delivered)},
        "rewards": [{"stage": "Cargo delivered", "type": "MissionReward_Money", "amount": money}],
    }


def store(kind, port, guids, weight=10.0):
    return {"kind": kind, "port": port and f"Port_{port}",
            "items": [{"guid": g, "name": "Crate", "weight": weight} for g in guids]}


class PlannerTests(unittest.TestCase):
    def test_item_locations_and_values(self):
        snap = snapshot([mission("m1", "B", ["g1", "g2", "g3", "g4"], delivered=["g4"])],
                        [store("warehouse", "A", ["g1"]), store("boatCargoArea", None, ["g2"])])
        by_guid = {i.guid: i for i in snap.items}
        self.assertEqual(by_guid["g1"].location, "Port_A")
        self.assertEqual(by_guid["g2"].location, BOAT)
        self.assertIsNone(by_guid["g3"].location)
        self.assertTrue(by_guid["g4"].delivered)
        self.assertEqual(by_guid["g1"].value, 25)
        self.assertEqual(by_guid["g4"].value, 0)
        self.assertEqual(snap.distance_km("Port_A", "Port_C"), 20)

    def test_cargo_aboard_the_driven_boat(self):
        data = {
            "ports": [], "player": {"location": [0.0, 0.0, 0.0]},
            "boats": [{"boatName": "Near", "owned": True, "location": [10.0, 0.0, 0.0], "path": "B /L/near.Boat:P.B_1"},
                      {"boatName": "Far", "owned": True, "location": [9000.0, 0.0, 0.0], "path": "B /L/far.Boat:P.B_1"},
                      {"boatName": "Barge", "owned": False, "location": [1.0, 0.0, 0.0], "path": "B /L/barge.Barge:P.B_1"}],
            "missions": [mission("m1", "B", ["g1", "g2"])],
            "containers": [
                {"kind": "boatCargoArea", "path": "A /L/near.Boat:P.A_1", "gridSize": [2, 2, 2], "occupied": [[0, 0, 0]] * 4,
                 "items": [{"guid": "g1", "name": "Crate", "weight": 5.0}, {"guid": "x", "name": "Spare", "weight": 1.0}]},
                {"kind": "boatCargoArea", "path": "A /L/far.Boat:P.A_1", "gridSize": [9, 9, 9],
                 "items": [{"guid": "g2", "name": "Crate", "weight": 5.0}]},
                {"kind": "boatCargoArea", "path": "A /L/barge.Barge:P.A_1", "items": [{"guid": "y", "name": "Blade"}]},
            ],
        }
        snap = parse_snapshot(data)
        self.assertEqual(snap.boat_name, "Near")
        self.assertEqual([(h.name, h.cargo.guid if h.cargo else None) for h in snap.aboard], [("Crate", "g1"), ("Spare", None)])
        self.assertEqual((snap.hold_size, snap.hold_cells_used), ((2, 2, 2), 4))

    def test_cargo_on_the_dock_is_told_apart_from_the_warehouse(self):
        snap = snapshot([mission("m1", "B", ["g1", "g2", "g3"])],
                        [store("warehouse", "A", ["g1"]), store("cargoArea", "A", ["g2"]), store("boatCargoArea", None, ["g3"])])
        by_guid = {i.guid: i for i in snap.items}
        self.assertEqual([by_guid[g].on_dock for g in ("g1", "g2", "g3")], [False, True, False])
        self.assertEqual([snap.place_name(by_guid[g]) for g in ("g1", "g2", "g3")], ["A", "A (On the Dock)", "On boat"])
        self.assertEqual(by_guid["g2"].location, "Port_A")  # still plannable from that port

    def test_contract_short_id_matches_the_game(self):
        # Read from the game's own mission view: this GUID is shown in game as #DL-E06448AD.
        snap = snapshot([mission("69FF7E4A49B24794656A5296F230547D", "B", ["g1"])], [])
        self.assertEqual(snap.missions[0].short_id, "#DL-E06448AD")
        self.assertEqual(snapshot([mission("m1", "B", ["g1"])], []).missions[0].short_id, "")

    def test_delivers_everything_and_respects_item_limit(self):
        snap = snapshot([mission("m1", "B", ["g1", "g2"]), mission("m2", "C", ["g3", "g4"])],
                        [store("warehouse", "A", ["g1", "g2", "g3", "g4"])])
        plan = make_plan(snap, "Port_A", Capacity(max_items=3), "full", max_stops=10)
        self.assertEqual(plan.delivered, 4)
        self.assertEqual(plan.left_behind, 0)
        self.assertEqual(plan.total_money, 200)
        self.assertTrue(all(stop.onboard_count <= 3 for stop in plan.stops))

    def test_weight_limit(self):
        snap = snapshot([mission("m1", "B", ["g1", "g2", "g3"])], [store("warehouse", "A", ["g1", "g2", "g3"])])
        plan = make_plan(snap, "Port_A", Capacity(max_weight=20), "stops", max_stops=10)
        self.assertEqual(len(plan.stops[0].load), 2)
        self.assertEqual(plan.delivered, 3)

    def test_cargo_stored_at_destination_is_handed_in(self):
        snap = snapshot([mission("m1", "A", ["g1"])], [store("warehouse", "A", ["g1"])])
        plan = make_plan(snap, "Port_A", Capacity(), "money")
        self.assertEqual([len(s.hand_in) for s in plan.stops], [1])
        self.assertEqual(plan.total_km, 0)

    def test_cargo_on_boat_is_delivered_first(self):
        snap = snapshot([mission("m1", "C", ["g1"])], [store("boatCargoArea", None, ["g1"])])
        plan = make_plan(snap, "Port_A", Capacity(), "stops")
        self.assertEqual([s.port for s in plan.stops], ["Port_A", "Port_C"])
        self.assertEqual(len(plan.stops[1].unload), 1)

    def test_deadheads_past_poor_cargo_to_a_better_port(self):
        # The boat holds one item. Taking the cheap crate at A blocks the valuable one waiting at B.
        snap = snapshot([mission("cheap", "C", ["g1"], money=10), mission("rich", "C", ["g2"], money=1000)],
                        [store("warehouse", "A", ["g1"]), store("warehouse", "B", ["g2"])])
        plan = make_plan(snap, "Port_A", Capacity(max_items=1), "money", max_stops=3)
        self.assertEqual([s.port for s in plan.stops], ["Port_A", "Port_B", "Port_C"])
        self.assertEqual(plan.stops[0].load, [])
        self.assertEqual(plan.total_money, 1000)
        self.assertEqual(plan.total_km, 20)

    def test_never_loads_cargo_it_does_not_deliver(self):
        # Two stops are not enough to reach C, so the crate for C must stay in the warehouse.
        snap = snapshot([mission("m1", "B", ["g1"]), mission("m2", "C", ["g2"])],
                        [store("warehouse", "A", ["g1", "g2"])])
        for priority in ("money", "stops", "full"):
            plan = make_plan(snap, "Port_A", Capacity(), priority, max_stops=2)
            unloaded = {i.guid for stop in plan.stops for i in stop.unload}
            loaded = [i.guid for stop in plan.stops for i in stop.load]
            self.assertTrue(loaded)
            self.assertTrue(set(loaded) <= unloaded, priority)
            # The crate for C still travels, but only as cargo being moved closer, labelled as such.
            self.assertEqual([(i.guid, port) for i, port in plan.stops[0].stage], [("g2", "Port_B")])
            self.assertEqual(plan.stops[0].onboard_count, 2)

    def test_undelivered_cargo_on_board_is_left_at_the_nearest_stop(self):
        ports = [{"id": f"Port_{name}", "name": name, "location": [x * 100_000.0, 0.0, 0.0]}
                 for name, x in (("A", 0), ("B", 10), ("C", 20), ("D", 24))]
        # g1 is on the boat for D, which two stops cannot reach. C is the closest the route gets to D.
        snap = parse_snapshot({"ports": ports, "missions": [mission("far", "D", ["g1"], money=1),
                                                             mission("near", "C", ["g2", "g3"], money=5000)],
                               "containers": [store("boatCargoArea", None, ["g1"]), store("warehouse", "A", ["g2", "g3"])]})
        plan = make_plan(snap, "Port_A", Capacity(), "money", max_stops=2)
        self.assertEqual([s.port for s in plan.stops], ["Port_A", "Port_C"])
        self.assertEqual([[i.guid for i in s.drop] for s in plan.stops], [[], ["g1"]])
        self.assertEqual([s.onboard_count for s in plan.stops], [3, 0])
        self.assertEqual((plan.delivered, plan.dropped), (2, 1))

    def test_spare_space_moves_cargo_closer(self):
        ports = [{"id": f"Port_{name}", "name": name, "location": [x * 100_000.0, 0.0, 0.0]}
                 for name, x in (("A", 0), ("B", 10), ("C", 20), ("D", 24))]
        # Two stops reach C. The crate for D cannot be delivered, but C is far nearer to D than A is.
        snap = parse_snapshot({"ports": ports, "missions": [mission("near", "C", ["g1"], money=5000),
                                                             mission("far", "D", ["g2"], money=1)],
                               "containers": [store("warehouse", "A", ["g1", "g2"])]})
        plan = make_plan(snap, "Port_A", Capacity(), "money", max_stops=2)
        self.assertEqual([s.port for s in plan.stops], ["Port_A", "Port_C"])
        self.assertEqual([(i.guid, port) for i, port in plan.stops[0].stage], [("g2", "Port_C")])
        self.assertEqual([i.guid for i in plan.stops[1].drop], ["g2"])
        self.assertEqual((plan.delivered, plan.staged, plan.dropped), (1, 1, 0))
        self.assertEqual([s.onboard_count for s in plan.stops], [2, 0])

        full = make_plan(snap, "Port_A", Capacity(max_items=1), "money", max_stops=2)
        self.assertEqual(full.staged, 0)  # no spare space, so nothing extra is carried

    def test_unload_all_only_when_the_boat_is_emptied(self):
        snap = snapshot([mission("m1", "B", ["g1", "g2"]), mission("m2", "C", ["g3"])],
                        [store("warehouse", "A", ["g1", "g2", "g3"])])
        plan = make_plan(snap, "Port_A", Capacity(), "stops", max_stops=3)
        self.assertEqual([s.port for s in plan.stops], ["Port_A", "Port_B", "Port_C"])
        self.assertEqual([s.unload_all for s in plan.stops], [False, False, True])

    def test_cargo_on_board_keeps_its_place_in_the_hold(self):
        # A 4x1x1 hold with a 2-cell crate sitting in the middle: 2 cells are free but not side by side.
        from saslogi.packing import Hold
        two = [(0, 0, 0), (1, 0, 0)]
        snap = snapshot([mission("m1", "C", ["g1"]), mission("m2", "B", ["g2"])],
                        [store("boatCargoArea", None, ["g1"]), store("warehouse", "A", ["g2"])])
        for item in snap.items:
            item.kind, item.cells = "crate", two
        aboard = next(i for i in snap.items if i.guid == "g1")
        aboard.placed = [(1, 0, 0), (2, 0, 0)]
        plan = make_plan(snap, "Port_A", Capacity(hold=Hold((4, 1, 1))), "stops", max_stops=2)
        self.assertEqual(plan.stops[0].load, [])
        aboard.placed = [(0, 0, 0), (1, 0, 0)]  # pushed to one end, the second crate fits beside it
        plan = make_plan(snap, "Port_A", Capacity(hold=Hold((4, 1, 1))), "stops", max_stops=3)
        self.assertEqual([i.guid for i in plan.stops[0].load], ["g2"])

    def test_placement_is_turned_the_way_the_game_turns_it(self):
        data = {"ports": [], "missions": [mission("m1", "B", ["g1", "g2"])],
                "footprints": {"bar": [[0, 0, 0], [1, 0, 0]]},
                "containers": [{"kind": "boatCargoArea", "path": "A /L/b.Boat:P.A_1", "gridSize": [6, 6, 1],
                                "items": [{"guid": "g1", "def": "bar"}, {"guid": "g2", "def": "bar"}, {"guid": "x", "def": "bar"}],
                                "placements": [{"guid": "g1", "position": [2, 3, 0], "yaw": 90.0},
                                               {"guid": "g2", "position": [2, 3, 0], "yaw": -90.0},
                                               {"guid": "x", "position": [5, 5, 0], "yaw": 180.0}]}]}
        snap = parse_snapshot(data)
        placed = {i.guid: i.placed for i in snap.items}
        self.assertEqual(placed["g1"], [(2, 3, 0), (2, 4, 0)])
        self.assertEqual(placed["g2"], [(2, 3, 0), (2, 2, 0)])
        self.assertEqual(snap.hold_blocked, [(5, 5, 0), (4, 5, 0)])  # not contract cargo: just in the way

    def test_partial_loads_can_be_told_from_whole_ones(self):
        # Three crates of one contract wait at A but the boat only takes two per trip.
        snap = snapshot([mission("m1", "B", ["g1", "g2", "g3"])], [store("warehouse", "A", ["g1", "g2", "g3"])])
        plan = make_plan(snap, "Port_A", Capacity(max_items=2), "stops", max_stops=4)
        self.assertEqual([s.port for s in plan.stops], ["Port_A", "Port_B", "Port_A", "Port_B"])
        waiting = waiting_by_contract(snap, plan)
        self.assertEqual([(len(s.load), w.get("m1", 0)) for s, w in zip(plan.stops, waiting)],
                         [(2, 3), (0, 0), (1, 1), (0, 0)])  # first pickup is partial, the second takes the rest

    def test_prefers_a_whole_contract_over_part_of_a_richer_one(self):
        # Room for two. The rich contract has three crates here, the modest one exactly two.
        snap = snapshot([mission("rich", "B", ["r1", "r2", "r3"], money=900), mission("modest", "B", ["m1", "m2"], money=100)],
                        [store("warehouse", "A", ["r1", "r2", "r3", "m1", "m2"])])
        plan = make_plan(snap, "Port_A", Capacity(max_items=2), "money", max_stops=2)
        self.assertEqual(sorted(i.guid for i in plan.stops[0].load), ["m1", "m2"])

    def test_still_splits_a_contract_when_nothing_fits_whole(self):
        snap = snapshot([mission("big", "B", ["g1", "g2", "g3"])], [store("warehouse", "A", ["g1", "g2", "g3"])])
        plan = make_plan(snap, "Port_A", Capacity(max_items=2), "money", max_stops=2)
        self.assertEqual(len(plan.stops[0].load), 2)

    def test_splits_can_be_switched_off(self):
        snap = snapshot([mission("big", "B", ["g1", "g2", "g3"]), mission("small", "C", ["g4"])],
                        [store("warehouse", "A", ["g1", "g2", "g3", "g4"])])
        plan = make_plan(snap, "Port_A", Capacity(max_items=2, allow_splits=False), "stops", max_stops=4)
        self.assertEqual([i.guid for stop in plan.stops for i in stop.load], ["g4"])  # the big contract never fits whole
        waiting = waiting_by_contract(snap, plan)
        for stop, here in zip(plan.stops, waiting):
            for item in stop.load:
                self.assertEqual(sum(i.mission is item.mission for i in stop.load), here[item.mission.id])

    def test_money_priority_keeps_going_after_a_rich_first_delivery(self):
        # One very rich crate next door, then ordinary work further out. A plain money-per-km ratio
        # would deliver the rich crate and stop, since everything after it lowers the average (682 vs 633 per km).
        ports = [{"id": f"Port_{name}", "name": name, "location": [x * 100_000.0, 0.0, 0.0]}
                 for name, x in (("A", 0), ("B", 1), ("C", 11), ("D", 21))]
        snap = parse_snapshot({"ports": ports,
                               "missions": [mission("rich", "B", ["g1"], money=9000), mission("m2", "C", ["g2"], money=6000),
                                            mission("m3", "D", ["g3"], money=6000)],
                               "containers": [store("warehouse", "A", ["g1"]), store("warehouse", "B", ["g2"]),
                                              store("warehouse", "C", ["g3"])]})
        plan = make_plan(snap, "Port_A", Capacity(), "money", max_stops=4)
        self.assertEqual([s.port for s in plan.stops], ["Port_A", "Port_B", "Port_C", "Port_D"])
        self.assertEqual(plan.total_money, 21000)

    def test_following_a_plan_ticks_off_work_instead_of_replanning(self):
        missions = [mission("m1", "B", ["g1", "g2"]), mission("m2", "C", ["g3"])]
        plan = make_plan(snapshot(missions, [store("warehouse", "A", ["g1", "g2", "g3"])]), "Port_A", Capacity(), "stops", 3)
        self.assertEqual([s.port for s in plan.stops], ["Port_A", "Port_B", "Port_C"])

        # One crate loaded so far: the first stop now only asks for the other two.
        later = snapshot(missions, [store("boatCargoArea", None, ["g1"]), store("warehouse", "A", ["g2", "g3"])])
        view = remaining(advance(plan, "Port_A"), later)
        self.assertEqual(sorted(i.guid for i in view.stops[0].load), ["g2", "g3"])
        self.assertEqual(sorted(i.guid for i in view.stops[1].unload), ["g1", "g2"])

        # At sea nothing moves on; docking at B drops the first stop; one of B's two crates is off already.
        at_b = snapshot([mission("m1", "B", ["g1", "g2"], delivered=["g1"]), missions[1]],
                        [store("boatCargoArea", None, ["g2", "g3"])])
        self.assertEqual(len(advance(plan, None).stops), 3)
        view = remaining(advance(plan, "Port_B"), at_b)
        self.assertEqual([s.port for s in view.stops], ["Port_B", "Port_C"])
        self.assertEqual([i.guid for i in view.stops[0].unload], ["g2"])
        self.assertTrue(has_work(view))

        self.assertIsNone(advance(plan, "Port_Elsewhere"))  # docked off the route: time for a new plan
        done = snapshot([mission("m1", "B", ["g1", "g2"], delivered=["g1", "g2"]),
                         mission("m2", "C", ["g3"], delivered=["g3"])], [])
        self.assertFalse(has_work(remaining(advance(plan, "Port_C"), done)))

    def test_load_check_compares_the_boat_with_the_current_stop(self):
        missions = [mission("m1", "B", ["g1", "g2"]), mission("m2", "C", ["g3"]), mission("m3", "C", ["g4"])]
        start = snapshot(missions[:2], [store("warehouse", "A", ["g1", "g2", "g3"])])
        plan = make_plan(start, "Port_A", Capacity(), "stops", 3)
        check = check_load(plan, start)
        self.assertEqual((check.planned, check.loaded, len(check.to_load), check.ready), (3, 0, 3, False))

        # Two of the three are aboard, plus a crate from a contract the plan knows nothing about.
        partly = snapshot(missions, [store("boatCargoArea", None, ["g1", "g3", "g4"]), store("warehouse", "A", ["g2"])])
        check = check_load(plan, partly)
        self.assertEqual((check.loaded, [i.guid for i in check.to_load], [i.guid for i in check.extra]), (2, ["g2"], ["g4"]))
        self.assertFalse(check.ready)

        done = snapshot(missions[:2], [store("boatCargoArea", None, ["g1", "g2", "g3"])])
        self.assertTrue(check_load(plan, done).ready)

        # At B the two crates for B must come off; one still aboard is reported.
        at_b = snapshot([mission("m1", "B", ["g1", "g2"], delivered=["g1"]), missions[1]],
                        [store("boatCargoArea", None, ["g2", "g3"])])
        check = check_load(advance(plan, "Port_B"), at_b)
        self.assertEqual(([i.guid for i in check.to_unload], check.extra, check.ready), (["g2"], [], False))

    def test_sea_distance_is_used_when_known(self):
        snap = snapshot([mission("m1", "B", ["g1"])], [store("warehouse", "A", ["g1"])])
        self.assertEqual(snap.distance_km("Port_A", "Port_B"), 10)
        snap.sea_km = {("Port_A", "Port_B"): 14.0, ("Port_B", "Port_A"): 14.0}
        self.assertEqual(snap.distance_km("Port_A", "Port_B"), 14.0)
        self.assertEqual(snap.distance_km("Port_A", "Port_C"), 20)  # no sea route known: straight line
        self.assertEqual(make_plan(snap, "Port_A", Capacity(), "stops", 2).total_km, 14.0)

    def test_stale_snapshot_warning(self):
        from saslogi.ui import stale_warning
        self.assertEqual(stale_warning(0), "")
        self.assertEqual(stale_warning(59), "")  # two missed 20-second exports is not yet a problem
        self.assertIn("for 1 minute.", stale_warning(60))
        self.assertIn("for 12 minutes.", stale_warning(12 * 60 + 30))
        self.assertIn("for 3 hours.", stale_warning(3 * 3600 + 5))
        self.assertIn("for 4 days.", stale_warning(4 * 86400 + 5))
        self.assertIn("F7", stale_warning(600))
        self.assertIn("F8", stale_warning(600))

    def test_planning_from_sea_starts_in_transit(self):
        # The boat is 4 km out from A, on the way towards B, carrying a crate for B and one for a port
        # the route will not reach.
        ports = [{"id": f"Port_{name}", "name": name, "location": [x * 100_000.0, 0.0, 0.0]}
                 for name, x in (("A", 0), ("B", 10), ("C", 20), ("D", 60))]
        data = {"ports": ports, "player": {"location": [400_000.0, 0.0, 0.0]},
                "boats": [{"owned": True, "location": [400_000.0, 0.0, 0.0], "path": "B /L/b.Boat:P.B"}],
                "missions": [mission("m1", "B", ["g1"]), mission("m2", "C", ["g2"]), mission("far", "D", ["g3"], money=1)],
                "containers": [{"kind": "boatCargoArea", "path": "A /L/b.Boat:P.A", "items": [
                    {"guid": "g1", "name": "Crate", "weight": 1.0}, {"guid": "g3", "name": "Crate", "weight": 1.0}]},
                    store("warehouse", "B", ["g2"])]}
        snap = parse_snapshot(data)
        self.assertEqual((snap.docked_port(), snap.current_port(), snap.here()), (None, "Port_A", AT_SEA))
        self.assertEqual(snap.port_name(AT_SEA), "In transit")
        self.assertEqual(snap.distance_km(AT_SEA, "Port_B"), 6)

        plan = make_plan(snap, snap.here(), Capacity(), "stops", max_stops=3)
        self.assertEqual([s.port for s in plan.stops], [AT_SEA, "Port_B", "Port_C"])
        self.assertEqual(plan.stops[1].leg_km, 6)  # measured from the boat, not from the port astern
        first = plan.stops[0]
        self.assertEqual((first.load, first.unload, first.drop), ([], [], []))  # nothing can be handled at sea
        self.assertEqual([i.guid for i in plan.stops[2].drop], ["g3"])  # left at the real stop nearest D

        # Still at sea nothing changes; docking at B drops the in-transit stop.
        self.assertEqual(len(advance(plan, snap.docked_port()).stops), 3)
        self.assertEqual([s.port for s in advance(plan, "Port_B").stops], ["Port_B", "Port_C"])

    def test_whole_contract_in_destination_warehouse_is_ready_to_complete(self):
        delivered = mission("done", "B", ["g1", "g2"], delivered=["g1", "g2"])
        partly = mission("partly", "B", ["g3", "g4"], delivered=["g3"])
        elsewhere = mission("elsewhere", "C", ["g5"])
        snap = snapshot([delivered, partly, elsewhere],
                        [store("warehouse", "B", ["g1", "g2", "g3"]), store("warehouse", "A", ["g4", "g5"])])
        ready = snap.completable_at("Port_B")
        self.assertEqual([m.id for m in ready], ["done"])
        self.assertEqual(sorted(i.guid for i in ready[0].to_take_out), ["g1", "g2"])
        self.assertEqual(snap.completable_at("Port_A"), [])  # cargo at a port that is not its destination
        self.assertEqual(snap.completable_at("Port_C"), [])

        # Already out on the dock: nothing left to take out. One still in the warehouse: just that one.
        on_dock = snapshot([delivered], [store("cargoArea", "B", ["g1", "g2"])])
        self.assertEqual(on_dock.completable_at("Port_B"), [])
        mixed = snapshot([delivered], [store("cargoArea", "B", ["g1"]), store("warehouse", "B", ["g2"])])
        self.assertEqual([i.guid for i in mixed.completable_at("Port_B")[0].to_take_out], ["g2"])

    def test_unloading_the_rest_of_a_contract_calls_for_the_stored_part(self):
        # Two of this contract's crates were delivered earlier and sit in B's warehouse; the boat brings the third.
        finishing = mission("finishing", "B", ["g1", "g2", "g3"], delivered=["g1", "g2"])
        # This one still has a crate back at A, so unloading at B does not finish it.
        unfinished = mission("unfinished", "B", ["g4", "g5", "g6"], delivered=["g4"])
        snap = snapshot([finishing, unfinished],
                        [store("warehouse", "B", ["g1", "g2", "g4"]), store("boatCargoArea", None, ["g3", "g5"]),
                         store("warehouse", "A", ["g6"])])
        plan = make_plan(snap, "Port_B", Capacity(), "stops", max_stops=1)
        stop = plan.stops[0]
        self.assertEqual(sorted(i.guid for i in stop.unload), ["g3", "g5"])
        found = completing_unloads(snap, stop)
        self.assertEqual([(m.id, sorted(i.guid for i in stored)) for m, stored in found], [("finishing", ["g1", "g2"])])
        self.assertEqual(snap.completable_at("Port_B"), [])  # not complete yet: one crate is still aboard

        # Stored part already out on the dock: nothing to take out of the warehouse.
        on_dock = snapshot([finishing], [store("cargoArea", "B", ["g1", "g2"]), store("boatCargoArea", None, ["g3"])])
        self.assertEqual(completing_unloads(on_dock, make_plan(on_dock, "Port_B", Capacity(), "stops", 1).stops[0]), [])

    def test_docked_only_when_close_to_a_port(self):
        data = {"ports": [{"id": "Port_A", "name": "A", "location": [0.0, 0.0, 0.0]},
                          {"id": "Port_B", "name": "B", "location": [1_000_000.0, 0.0, 0.0]}],
                "boats": [{"owned": True, "location": [10_000.0, 0.0, 0.0], "path": "B /L/b.Boat:P.B"}],
                "player": {"location": [10_000.0, 0.0, 0.0]}}
        self.assertEqual(parse_snapshot(data).docked_port(), "Port_A")
        data["boats"][0]["location"] = data["player"]["location"] = [300_000.0, 0.0, 0.0]
        snap = parse_snapshot(data)
        self.assertEqual((snap.current_port(), snap.docked_port()), ("Port_A", None))

    def test_stop_limit_reports_what_is_left(self):
        snap = snapshot([mission("m1", "B", ["g1"]), mission("m2", "C", ["g2"])],
                        [store("warehouse", "A", ["g1", "g2"])])
        plan = make_plan(snap, "Port_A", Capacity(), "full", max_stops=2)
        self.assertEqual(len(plan.stops), 2)
        self.assertEqual(plan.left_behind, 1)

    def test_cargo_the_operator_could_not_fit_is_not_loaded(self):
        snap = snapshot([mission("m1", "B", ["g1", "g2"])], [store("warehouse", "A", ["g1", "g2"])])
        plan = make_plan(snap, "Port_A", Capacity(), "full", max_stops=10, no_room={"G1"})
        loaded = {item.guid for stop in plan.stops for item in stop.load}
        self.assertEqual(loaded, {"g2"})

    def test_cargo_already_on_the_boat_is_not_dropped_by_a_report(self):
        snap = snapshot([mission("m1", "B", ["g1"])], [store("boatCargoArea", None, ["g1"])])
        plan = make_plan(snap, "Port_A", Capacity(), "full", max_stops=10, no_room={"G1"})
        self.assertEqual(plan.delivered, 1)

    def test_reading_the_operators_report(self):
        import tempfile
        from pathlib import Path
        from saslogi.model import read_no_room
        with tempfile.TemporaryDirectory() as folder:
            snapshot_path = Path(folder) / "snapshot.json"
            self.assertEqual(read_no_room(snapshot_path), frozenset())
            (Path(folder) / "loadfail.txt").write_text("abc123\n\nDEF456\n", encoding="ascii")
            self.assertEqual(read_no_room(snapshot_path), frozenset({"ABC123", "DEF456"}))

    def test_contract_deadline_and_the_game_clock(self):
        from saslogi.model import TICKS_PER_HOUR
        ports = [{"id": "Port_A", "name": "A", "location": [0.0, 0.0, 0.0]}]
        urgent = mission("m1", "B", ["g1"])
        urgent["deadlineTicks"] = str(1_000 * TICKS_PER_HOUR + 6 * TICKS_PER_HOUR)
        plain = mission("m2", "B", ["g2"])
        snap = parse_snapshot({"time": "t", "ports": ports, "missions": [urgent, plain],
                               "containers": [store("warehouse", "A", ["g1", "g2"])],
                               "calendar": {"nowTicks": str(1_000 * TICKS_PER_HOUR), "gameSpeed": 8.0}})
        by_id = {m.id: m for m in snap.missions}
        self.assertEqual(snap.hours_left(by_id["m1"]), 6.0)
        self.assertIsNone(snap.hours_left(by_id["m2"]))
        self.assertEqual(snap.game_speed, 8.0)

    def test_delivered_cargo_still_in_the_hold_takes_up_room(self):
        data = {"ports": [{"id": "Port_A", "name": "A", "location": [0.0, 0.0, 0.0]},
                          {"id": "Port_B", "name": "B", "location": [1_000_000.0, 0.0, 0.0]}],
                "missions": [{**mission("m1", "B", ["g1"], delivered=["g1"]), "status": "Succeeded"},
                             mission("m2", "B", ["g2"])],  # m1 is over, but its piece is still in the hold
                "footprints": {"bar": [[0, 0, 0], [1, 0, 0]]},
                "containers": [{"kind": "boatCargoArea", "path": "A /L/b.Boat:P.A_1", "gridSize": [2, 1, 1],
                                "items": [{"guid": "g1", "def": "bar"}],
                                "placements": [{"guid": "g1", "position": [0, 0, 0], "yaw": 0.0}]},
                               {"kind": "warehouse", "port": "Port_A",
                                "items": [{"guid": "g2", "def": "bar", "name": "Crate", "weight": 10.0}]}]}
        snap = parse_snapshot(data)
        self.assertEqual(snap.hold_blocked, [(0, 0, 0), (1, 0, 0)])
        self.assertEqual([i.guid for i in snap.delivered_aboard], ["g1"])
        plan = make_plan(snap, "Port_A", Capacity(hold=Hold((2, 1, 1))), "stops", max_stops=3)
        self.assertEqual(plan.stops[0].load, [])  # the room is not there to use

    def test_delivered_cargo_found_away_from_its_destination_is_still_to_deliver(self):
        # The game keeps g1 and g2 marked delivered. g1 was taken out of B's warehouse and is on the boat; g2
        # waits in B's warehouse (delivered, to be taken out); g3 is nowhere (consumed on delivery).
        snap = snapshot([mission("m1", "B", ["g1", "g2", "g3"], delivered=["g1", "g2", "g3"])],
                        [store("boatCargoArea", None, ["g1"]), store("warehouse", "B", ["g2"])])
        by_guid = {i.guid: i for i in snap.items}
        self.assertFalse(by_guid["g1"].delivered)
        self.assertTrue(by_guid["g2"].delivered)
        self.assertTrue(by_guid["g3"].delivered)
        self.assertEqual([i.guid for i in snap.unmarked], ["g1"])
        self.assertEqual([i.guid for i in snap.undelivered()], ["g1"])
        self.assertGreater(by_guid["g1"].value, 0)


if __name__ == "__main__":
    unittest.main()


class CompletionPaysTests(unittest.TestCase):
    """The game pays a contract when its last piece is delivered, so unfinished contracts earn nothing yet."""

    def test_only_a_finished_contract_earns_and_it_earns_the_whole_contract(self):
        snap = snapshot([mission("m1", "B", ["g1", "g2"])], [store("warehouse", "A", ["g1", "g2"])])
        plan = make_plan(snap, "Port_A", Capacity(max_items=1), "full", max_stops=10)
        self.assertEqual(plan.completed, 1)
        self.assertEqual([s.earned for s in plan.stops if s.unload], [0.0, 100.0])  # nothing until the last piece
        self.assertEqual(plan.total_money, 100.0)

    def test_a_short_route_that_leaves_a_contract_half_done_earns_nothing_for_it(self):
        snap = snapshot([mission("m1", "B", ["g1", "g2"])], [store("warehouse", "A", ["g1", "g2"])])
        plan = make_plan(snap, "Port_A", Capacity(max_items=1), "full", max_stops=2)
        self.assertEqual(plan.completed, 0)
        self.assertEqual(plan.total_money, 0.0)
