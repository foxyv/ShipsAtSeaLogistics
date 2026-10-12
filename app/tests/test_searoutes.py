import os
import tempfile
import unittest

from saslogi import searoutes as sr

CELL = 5000.0  # cm, so 20 cells make a kilometre


def grid_from(picture):
    """A depth grid from rows of text: '.' is deep water, ':' is too shallow for boats, '#' is land."""
    rows = [line for line in picture.strip().split("\n")]
    depth = [{".": 30, ":": 2, "#": -10}[ch] for line in rows for ch in line.strip()]
    return sr.DepthGrid(0.0, 0.0, CELL, len(rows[0].strip()), len(rows), depth, header="test")


def at(col, row):
    return (col * CELL, row * CELL)


class SeaRouteTests(unittest.TestCase):
    def test_open_water_route_is_the_straight_line(self):
        grid = grid_from("\n".join(["." * 30] * 20))
        routes = sr.compute_routes(grid, {"A": at(2, 3), "B": at(27, 15)})
        straight = ((25 ** 2 + 12 ** 2) ** 0.5) * CELL / 100_000
        self.assertAlmostEqual(routes["A|B"]["km"], straight, delta=straight * 0.01)

    def test_route_goes_round_land(self):
        grid = grid_from("""
            ....................
            ....................
            ..........#.........
            ..........#.........
            ..........#.........
            ..........#.........
            ..........#.........
            ..........#.........
            ..........#.........
            ..........#.........
        """)
        routes = sr.compute_routes(grid, {"A": at(5, 8), "B": at(15, 8)})
        straight = 10 * CELL / 100_000
        self.assertGreater(routes["A|B"]["km"], straight * 1.5)  # up and over the top of the wall
        self.assertLess(routes["A|B"]["km"], straight * 2.2)
        nav = grid.navigable()
        for x, y in routes["A|B"]["points"]:
            col, row = grid.index(x, y)
            self.assertTrue(nav[row * grid.cols + col])

    def test_shallow_water_is_not_navigable_but_ports_reach_past_it(self):
        grid = grid_from("""
            :::.......
            :::.......
            :::.......
        """)
        nav = grid.navigable()
        self.assertEqual(sr.nearest_navigable(grid, nav, *at(0, 1)), 1 * grid.cols + 3)
        routes = sr.compute_routes(grid, {"A": at(0, 1), "B": at(9, 1)})
        self.assertAlmostEqual(routes["A|B"]["km"], 9 * CELL / 100_000, places=3)

    def test_ports_with_no_water_between_them_get_no_route(self):
        grid = grid_from("""
            ....#....
            ....#....
            ....#....
        """)
        routes = sr.compute_routes(grid, {"A": at(1, 1), "B": at(7, 1), "C": at(2, 2)})
        self.assertEqual(sorted(routes), ["A|C"])
        table = sr.distances(routes)
        self.assertEqual(table[("A", "C")], table[("C", "A")])
        self.assertNotIn(("A", "B"), table)

    def test_diagonal_steps_do_not_cut_a_land_corner(self):
        grid = grid_from("""
            .#
            #.
        """)
        self.assertEqual(sr.compute_routes(grid, {"A": at(0, 0), "B": at(1, 1)}), {})

    def test_grid_file_and_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "depthgrid.txt")
            with open(path, "w", encoding="ascii") as file:
                file.write("sasdepth 1 -100 200 5000 3 2 500\n30 -99 2\n-0 999 5\n")
            grid = sr.load_depth_grid(path)
            self.assertEqual((grid.cols, grid.rows, grid.depth_m), (3, 2, [30, -99, 2, 0, 999, 5]))
            self.assertEqual(list(grid.navigable()), [1, 0, 0, 0, 1, 1])
            self.assertEqual(grid.position(4), (4900.0, 5200.0))

            with open(path, "w", encoding="ascii") as file:
                file.write("sasdepth 1 0 0 5000 3 2 500\n30 30 30\n")  # a row short: still being written
            self.assertIsNone(sr.load_depth_grid(path))
            self.assertIsNone(sr.load_depth_grid(os.path.join(folder, "missing.txt")))

            cache = os.path.join(folder, "searoutes.json")
            ports = {"A": (0.0, 0.0), "B": (10000.0, 0.0)}
            wanted = sr.signature(grid, ports)
            sr.save_cache(cache, wanted, {"A|B": {"km": 1.5, "points": []}})
            self.assertEqual(sr.load_cache(cache, wanted)["A|B"]["km"], 1.5)
            self.assertIsNone(sr.load_cache(cache, sr.signature(grid, {**ports, "C": (1.0, 1.0)})))


if __name__ == "__main__":
    unittest.main()
