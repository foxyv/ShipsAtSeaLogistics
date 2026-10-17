import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:  # the window tests need PySide6
    QApplication = None

from tests.test_planner import mission, store
from tests.test_scoring import world


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class PlanningThreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # The window saves its settings as it plans. These tests keep them in a folder of their own,
        # never the planner's real ones, and read no real snapshot.
        cls.settings_dir = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.settings_dir.cleanup()

    def setUp(self):
        from saslogi import ui
        self.ui = ui
        self.window = ui.MainWindow(QSettings(os.path.join(self.settings_dir.name, "planner.ini"), QSettings.IniFormat),
                                    path=os.path.join(self.settings_dir.name, "no snapshot here.json"))
        self.window.snap = world([mission("m1", "B", ["g1"]), mission("m2", "C", ["g2"])],
                                 [store("warehouse", "A", ["g1", "g2"])])
        self.window.start_port.addItem("A", "Port_A")

    def tearDown(self):
        self.wait()
        self.window.close()

    def wait(self, limit_ms=15000):
        waited = 0
        while self.window.planning and waited < limit_ms:
            QTest.qWait(20)
            waited += 20
        self.assertFalse(self.window.planning, "planning did not finish")

    def test_planning_runs_off_the_window_thread_and_greys_the_old_plan(self):
        self.window.replan()
        self.assertTrue(self.window.planning)
        self.assertFalse(self.window.busy.isHidden())  # the busy bar is up
        self.assertFalse(self.window.plan_tree.isEnabled())  # and the old plan is greyed
        self.wait()
        QTest.qWait(50)
        self.assertTrue(self.window.busy.isHidden())
        self.assertTrue(self.window.plan_tree.isEnabled())
        self.assertIsNotNone(self.window.committed)
        self.assertGreater(self.window.plan_tree.topLevelItemCount(), 0)

    def test_changes_while_planning_are_planned_once_more_with_the_latest_settings(self):
        runs = []
        real = self.ui.make_plan

        def counting(*args, **kwargs):
            runs.append(args[4])  # max stops
            return real(*args, **kwargs)

        self.ui.make_plan = counting
        try:
            self.window.max_stops.setValue(3)
            self.window.replan()
            self.window.max_stops.setValue(5)
            self.window.replan()
            self.window.max_stops.setValue(7)
            self.window.replan()
            self.wait()
        finally:
            self.ui.make_plan = real
        self.assertEqual(runs, [3, 7])  # the middle request was overtaken by the last
        self.assertTrue(self.window.plan_tree.isEnabled())


if __name__ == "__main__":
    unittest.main()
