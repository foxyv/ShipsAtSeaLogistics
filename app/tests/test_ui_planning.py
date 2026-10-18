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
                                    path=os.path.join(self.settings_dir.name, "no snapshot here.json"),
                                    ledger_path=os.path.join(self.settings_dir.name, "ledger.json"))
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

    def test_the_window_can_be_made_small_and_the_route_controls_scroll(self):
        controls = self.window.route_controls
        self.assertIs(controls.widget().parent(), controls.viewport())  # the controls sit in a scroll area
        # Nothing holds the window wide or tall: the status line wraps, the controls scroll.
        hint = self.window.minimumSizeHint()
        self.assertLess(hint.width(), 700)
        self.assertLess(hint.height(), 450)
        self.window.tabs.setCurrentWidget(controls.parentWidget())
        self.window.resize(hint.width(), hint.height())
        self.window.show()
        QTest.qWait(50)
        # Small enough, the controls are taller than the room they have and can be scrolled.
        self.assertGreater(controls.verticalScrollBar().maximum(), 0)


    # ------------------------------------------------------------------------------------------ transactions

    def give_ledger(self, rows, capital_raw=1000000):
        self.window.snap.finance = {"transactions": rows, "capitalRaw": str(capital_raw), "capital": capital_raw / 100,
                                    "historySize": 100}
        self.window._update_transactions()

    @staticmethod
    def row(day, raw, description):
        return {"day": day, "month": 9, "year": 1820, "raw": str(raw), "description": description}

    def shown(self):
        table = self.window.transactions
        return [[table.item(r, c).text() for c in range(5)] for r in range(table.rowCount())]

    def test_the_transactions_tab_lists_the_ledger_newest_first_with_exact_amounts(self):
        self.give_ledger([self.row(3, 1250050, "Mission completed: Fish to Fenes"), self.row(4, -30000, "Port fee")])
        self.assertEqual(self.window.tabs.tabText(self.window.tabs.indexOf(self.window.transactions.parentWidget())),
                         "Transactions")
        self.assertEqual(self.shown(), [
            ["1820-10-04", "Port fee", "Spent", "-300.00", "10 000.00"],
            ["1820-10-03", "Mission completed: Fish to Fenes", "Earned", "12 500.50", "10 300.00"],
        ])
        self.assertIn("2 of 2 transactions", self.window.tx_summary.text())
        self.assertIn("capital now 10 000.00", self.window.tx_summary.text())

    def test_the_filter_narrows_the_list_and_the_summary(self):
        self.give_ledger([self.row(3, 1250050, "Mission completed: Fish to Fenes"), self.row(4, -30000, "Port fee")])
        self.window.tx_filter.setText("mission")
        self.assertEqual([r[1] for r in self.shown()], ["Mission completed: Fish to Fenes"])
        self.assertIn("1 of 2 transactions", self.window.tx_summary.text())

    def test_exporting_writes_the_shown_rows_oldest_first(self):
        from unittest import mock
        self.give_ledger([self.row(3, 1250050, "Mission completed: Fish to Fenes"), self.row(4, -30000, "Port fee")])
        target = os.path.join(self.settings_dir.name, "out.csv")
        with mock.patch.object(self.ui.QFileDialog, "getSaveFileName", return_value=(target, "")):
            self.window.export_transactions()
        lines = open(target, encoding="utf-8-sig").read().splitlines()
        self.assertEqual(lines[0], "Date,Description,Type,Amount,Balance after")
        self.assertEqual(lines[1], "1820-10-03,Mission completed: Fish to Fenes,Earned,12500.50,10300.00")
        self.assertEqual(lines[2], "1820-10-04,Port fee,Spent,-300.00,10000.00")

    def test_a_snapshot_without_finance_leaves_the_tab_asking_for_the_mod(self):
        self.window.snap.finance = None
        self.window._update_transactions()
        self.assertIn("No transactions yet", self.window.tx_summary.text())


    # ------------------------------------------------------------------------------------------ unplanned cargo

    def test_cargo_that_came_aboard_after_the_plan_is_unloaded_at_its_port_and_the_route_is_left_alone(self):
        from saslogi.planner import Plan, Stop
        self.window.snap = world(
            [mission("m1", "B", ["g1"]), mission("m2", "B", ["g2"]), mission("m3", "C", ["g3"])],
            [store("boatCargoArea", None, ["g1", "g2", "g3"])])
        self.window.snap.docked_port = lambda: "Port_B"
        items = {i.guid: i for i in self.window.snap.items}
        planned = Plan(stops=[Stop(port="Port_B", unload=[items["g1"]]), Stop(port="Port_C")])
        self.window.committed = planned
        self.window.follow()
        folder = os.path.dirname(self.window.path)
        orders = open(os.path.join(folder, "crane_orders.txt")).read().splitlines()
        self.assertIn("g1 U", orders)
        self.assertIn("g2 U", orders)  # came aboard after the plan, but is for this port
        self.assertNotIn("g3 U", orders)  # for C
        self.assertFalse(self.window.planning)  # and the route is not changed behind the player's back
        self.assertEqual(self.window.committed, planned)  # the same route
        self.assertEqual([i.guid for i in self.window.strays], ["g3"])  # pointed out in the route
        first = self.window.plan_tree.topLevelItem(0)
        notes = [first.child(n).text(0) for n in range(first.childCount())]
        self.assertTrue(any("not in this route" in text for text in notes))


if __name__ == "__main__":
    unittest.main()
