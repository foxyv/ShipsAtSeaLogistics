import random
import unittest

from tests import sim_loading as sim


class LoadingSimulationTests(unittest.TestCase):
    """The simulated loading of real cargo footprints (see sim_loading.py) with a few trials each."""

    def failures(self, order, trials=40):
        rng = random.Random(7)
        return sum(not sim.trial(rng, sim.POLICIES["operator now (neat, then first-fit)"], order)[0] for _ in range(trials))

    def test_planner_order_is_always_placeable_by_the_planners_own_policy(self):
        rng = random.Random(7)
        self.assertTrue(all(sim.trial(rng, sim.first_fit, "planner order")[0] for _ in range(40)))

    def test_floor_area_first_fails_less_than_arrival_order(self):
        self.assertLess(self.failures("floor area, then height"), self.failures("shuffled"))


if __name__ == "__main__":
    unittest.main()
