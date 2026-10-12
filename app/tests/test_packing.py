import unittest

from saslogi.model import CargoItem, Mission
from saslogi.packing import Hold, rotations
from saslogi.planner import Capacity


def box(wx, wy, wz):
    return [(x, y, z) for x in range(wx) for y in range(wy) for z in range(wz)]


def item(kind, cells, size=2):
    mission = Mission("m", "m", "InProgress", "", None, "Port_B")
    return CargoItem(guid=kind, mission=mission, kind=kind, cells=cells, size=size, weight=10)


class PackingTests(unittest.TestCase):
    def test_rotations(self):
        self.assertEqual(len(rotations(box(4, 4, 4))), 1)
        self.assertEqual(len(rotations(box(4, 2, 1))), 2)

    def test_nine_pallets_fill_a_12x12x4_hold(self):
        load = Capacity(hold=Hold((12, 12, 4))).start()
        added = sum(load.add(item("pallet", box(4, 4, 4))) for _ in range(12))
        self.assertEqual(added, 9)
        self.assertEqual(load.fill(), 1.0)

    def test_long_item_is_rotated_to_fit(self):
        self.assertIsNotNone(Hold((4, 12, 4)).place(0, "pipe", box(9, 2, 2)))
        self.assertIsNone(Hold((4, 8, 4)).place(0, "pipe", box(9, 2, 2)))

    def test_stacking_switch(self):
        flat = box(4, 2, 1)
        stacked = Capacity(hold=Hold((4, 2, 4), stacking=True)).start()
        floor_only = Capacity(hold=Hold((4, 2, 4), stacking=False)).start()
        self.assertEqual(sum(stacked.add(item("flat", flat)) for _ in range(6)), 4)
        self.assertEqual(sum(floor_only.add(item("flat", flat)) for _ in range(6)), 1)

    def test_stacked_cargo_needs_full_support(self):
        hold = Hold((4, 4, 4))
        occupied = hold.place(0, "small", box(2, 2, 2))
        self.assertIsNone(hold.place(occupied, "wide", box(4, 4, 2)))
        self.assertIsNotNone(hold.place(occupied, "same", box(2, 2, 2)))

    def test_hold_rejects_unsupported_cargo_size(self):
        load = Capacity(hold=Hold((12, 12, 4), sizes=[0, 1, 2])).start()
        self.assertFalse(load.add(item("blade", box(2, 2, 1), size=5)))
        self.assertTrue(load.add(item("crate", box(2, 2, 1), size=2)))


if __name__ == "__main__":
    unittest.main()
