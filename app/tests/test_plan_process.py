"""The route planner in a process of its own: it must give the same plan, survive a dead worker, and stop with the
window. (On a thread of the window's process it held the interpreter's lock and froze the window for seconds.)"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:  # the window tests need PySide6
    QApplication = None

from saslogi.planner import Capacity, make_plan

from tests.test_planner import mission, store
from tests.test_scoring import world


def job_for(snap):
    from saslogi.scoring import Weights
    return {"snap": snap, "start": "Port_A", "capacity": Capacity(), "priority": "full", "max_stops": 5,
            "no_room": frozenset(), "weights": Weights()}


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class PlanProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from saslogi.ui import PlanRunner
        self.runner = PlanRunner(in_process=True)
        self.results = []
        self.runner.finished.connect(lambda plan, error: self.results.append((plan, error)))
        self.snap = world([mission("m1", "B", ["g1"]), mission("m2", "C", ["g2"])], [store("warehouse", "A", ["g1", "g2"])])

    def tearDown(self):
        self.runner.stop()

    def wait_for(self, count, limit_ms=30000):
        waited = 0
        while len(self.results) < count and waited < limit_ms:
            QTest.qWait(50)
            waited += 50
        self.assertGreaterEqual(len(self.results), count, "the planner process did not answer")

    def test_a_plan_made_in_the_worker_process_is_the_plan_made_here(self):
        self.runner.run(job_for(self.snap))
        self.wait_for(1)
        plan, error = self.results[0]
        self.assertIsNone(error)
        expected = make_plan(self.snap, "Port_A", Capacity(), "full", 5)
        self.assertEqual([s.port for s in plan.stops], [s.port for s in expected.stops])
        self.assertEqual([[i.guid for i in s.load] for s in plan.stops], [[i.guid for i in s.load] for s in expected.stops])
        self.assertTrue(self.runner._process.is_alive())

    def test_the_worker_is_kept_for_the_next_job(self):
        self.runner.run(job_for(self.snap))
        self.wait_for(1)
        first = self.runner._process.pid
        self.runner.run(job_for(self.snap))
        self.wait_for(2)
        self.assertEqual(self.runner._process.pid, first)

    def test_a_dead_worker_is_replaced_on_the_next_job(self):
        self.runner.run(job_for(self.snap))
        self.wait_for(1)
        self.runner._process.terminate()
        self.runner._process.join(5)
        self.runner.run(job_for(self.snap))
        self.wait_for(2)
        self.assertIsNone(self.results[1][1])
        self.assertIsNotNone(self.results[1][0])

    def test_an_error_in_the_planner_is_reported_and_the_worker_goes_on(self):
        bad = job_for(self.snap)
        bad["capacity"] = None  # makes the search fail inside the worker
        self.runner.run(bad)
        self.wait_for(1)
        self.assertIsNone(self.results[0][0])
        self.assertIsNotNone(self.results[0][1])
        self.runner.run(job_for(self.snap))
        self.wait_for(2)
        self.assertIsNone(self.results[1][1])

    def test_stopping_ends_the_worker(self):
        self.runner.run(job_for(self.snap))
        self.wait_for(1)
        process = self.runner._process
        self.runner.stop()
        process.join(5)
        self.assertFalse(process.is_alive())
