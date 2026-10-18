"""PySide6 window: contracts (the spreadsheet replacement), cargo by port, and the route planner."""

from __future__ import annotations

import copy
import os
import re
import sys
from collections import Counter, defaultdict
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from . import searoutes
from .mapview import MapView
from .audit import Audit, counts_of, items_of, plan_of
from .ledger import Ledger, exact, kind, write_csv
from .model import BOAT, Snapshot, load_snapshot, read_no_room
from .packing import Hold
from .planner import (
    PRIORITIES, Capacity, advance, check_load, completing_unloads, has_work, make_plan, remaining,
    unplanned_aboard, with_unplanned_unloads,
    waiting_by_contract,
)
from .scoring import PRESETS, Weights

# With auto export on the mod writes a snapshot every 20 seconds. Three missed in a row means it is
# off, which is the case after every game start, or the game is not running.
STALE_AFTER_SECONDS = 60


def stale_warning(age_seconds: float) -> str:
    """The warning to show for a snapshot this old, or "" while it is fresh enough."""
    if age_seconds < STALE_AFTER_SECONDS:
        return ""
    minutes = int(age_seconds // 60)
    if minutes < 60:
        ago = f"{minutes} minute{'' if minutes == 1 else 's'}"
    else:
        hours = minutes // 60
        ago = f"{hours} hour{'' if hours == 1 else 's'}" if hours < 48 else f"{hours // 24} days"
    return (f"No update from the game for {ago}. What you see here may be out of date. "
            "In game, press F7 to turn telemetry back on, or F8 for a single snapshot.")


MOD_RELATIVE = r"steamapps\common\Ships At Sea\SAS\Binaries\Win64\ue4ss\Mods\SasLogistics\snapshot.json"


# Distances are kept in km throughout; these are only for showing them. (label, km in one unit)
DISTANCE_UNITS = {
    "km": ("Kilometres (km)", 1.0),
    "nm": ("Nautical miles (nm)", 1.852),
    "mi": ("Miles (mi)", 1.609344),
}


def default_audit_path() -> str:
    """Where the planner writes its audit log: every plan it works out, and every change in the orders sent."""
    base = os.environ.get("APPDATA") or str(Path.home())
    return str(Path(base) / "SasLogistics" / "audit.jsonl")


def default_ledger_path() -> str:
    """Where the planner keeps its history of transactions: its own folder, not the mod's (which is deleted when
    the mod is removed)."""
    base = os.environ.get("APPDATA") or str(Path.home())
    return str(Path(base) / "SasLogistics" / "ledger.json")


def default_snapshot_path() -> str:
    """Looks for the mod's snapshot in every Steam library on this machine."""
    steam = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"
    libraries = [steam]
    try:
        text = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        libraries += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    except OSError:
        pass
    for library in libraries:
        if (library / MOD_RELATIVE).parent.exists():
            return str(library / MOD_RELATIVE)
    return str(steam / MOD_RELATIVE)


def money(value: float) -> str:
    return f"{value:,.0f}"


class NumberItem(QTableWidgetItem):
    """Table cell that shows formatted text but sorts by the number behind it."""

    def __init__(self, value: float, text: str | None = None):
        super().__init__(text if text is not None else money(value))
        self.value = value
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other):
        return self.value < getattr(other, "value", 0)


def summarise(items, width: int = 70) -> str:
    """Cargo names with counts, broken onto further lines once a line passes `width` characters."""
    counts = Counter(i.name for i in items)
    lines = [""]
    for name, n in sorted(counts.items()):
        part = f"{name} ×{n}" if n > 1 else name
        if lines[-1] and len(lines[-1]) + len(part) + 2 > width:
            lines[-1] += ","
            lines.append(part)
        else:
            lines[-1] += (", " if lines[-1] else "") + part
    return "\n".join(lines)


class PlanRunner(QObject):
    """Plans a route on a thread of its own. `finished` is emitted back on the window's thread with the
    plan, or with the error that stopped the search."""

    finished = Signal(object, object)

    def run(self, job):
        def work():
            try:
                plan = make_plan(job["snap"], job["start"], job["capacity"], job["priority"], job["max_stops"],
                                 no_room=job["no_room"], weights=job["weights"])
                self.finished.emit(plan, None)
            except Exception as error:  # shown in the window; the search must not take the program down
                self.finished.emit(None, error)

        threading.Thread(target=work, name="route planner", daemon=True).start()


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None, path: str | None = None, ledger_path: str | None = None,
                 audit_path: str | None = None):
        super().__init__()
        self.setWindowTitle("Ships At Sea Logistics")
        self.resize(1150, 720)
        self.settings = settings or QSettings("SasLogistics", "Planner")  # tests pass settings of their own
        self.path = path or self.settings.value("snapshotPath", default_snapshot_path())
        self.snap = Snapshot()
        self.audit = Audit(audit_path or default_audit_path())  # what the planner decided, line by line
        self.plan_cause = ""  # why the next route is being worked out, for the audit log
        self.ledger = Ledger(ledger_path or default_ledger_path())  # transactions, kept beyond what the game remembers
        self.mtime = None
        self.sea_km: dict = {}  # (port, port) -> km by sea; empty until sea routes are known
        self.sea_routes: dict = {}  # "A|B" -> the route itself, for drawing on the map
        self.sea_source = None  # what the routes in use were worked out from: (depth grid mtime, port positions)
        self.sea_job = None  # a background route calculation in progress
        self.sea_note = "Distances are straight lines between ports."
        self.no_room = frozenset()  # cargo the crane operator reports does not fit the hold
        self.planning = False  # a route is being worked out on the planner thread
        self.plan_again = False  # something changed meanwhile, so plan again when it finishes
        self.plan_job = None
        self.runner = PlanRunner()
        self.runner.finished.connect(self._planned)
        sys.setswitchinterval(0.002)  # a busy planner thread must not make the window sluggish
        self.committed = None  # the plan being followed; it is kept until something calls for a new one
        self.strays = []  # cargo on the boat that the route being followed does not mention
        self.replan_reason, self.replan_reason_at = "", ""  # why the route last changed by itself
        self.committed_at = ""
        self.known_cargo: set[str] = set()  # cargo that existed when that plan was made

        self.status = QLabel()
        self.status.setWordWrap(True)  # a long path would otherwise stop the window being made narrow
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # and so would its longest word
        self.stale = QLabel()
        self.stale.setWordWrap(True)
        self.stale.setStyleSheet("color: #e5484d; font-weight: bold;")  # a red that reads on light and dark themes
        self.stale.hide()
        self.units = QComboBox()
        self.units.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.units.setMinimumContentsLength(10)
        self.units.setToolTip("Units for every distance shown. Routes are not replanned when this changes.")
        for key, (label, _) in DISTANCE_UNITS.items():
            self.units.addItem(label, key)
        self.units.setCurrentIndex(max(0, self.units.findData(self.settings.value("distanceUnit", "km"))))
        browse = QPushButton("Snapshot file…")
        browse.clicked.connect(self.browse)
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.units)
        top.addWidget(browse)

        self.contracts = self._table(["ID", "Contract", "From", "To", "Delivered", "Cargo is at", "kg left", "Money left", "Status"])
        self.matrix = self._table(["Stored at", "Where", "Deliver to", "Items", "kg", "Money", "km"])
        self.tabs = QTabWidget()
        self.tabs.addTab(self.contracts, "Contracts")
        self.aboard = self._table(["Cargo", "ID", "Contract", "Deliver to", "kg", "Money", "km from here"])
        self.aboard_summary = QLabel()
        self.aboard_summary.setWordWrap(True)
        aboard_layout = QVBoxLayout()
        aboard_layout.addWidget(self.aboard_summary)
        aboard_layout.addWidget(self.aboard)
        aboard_tab = QWidget()
        aboard_tab.setLayout(aboard_layout)
        self.tabs.addTab(aboard_tab, "On board")
        self.tabs.addTab(self.matrix, "Cargo by port")
        self.tabs.addTab(self._transactions_tab(), "Transactions")
        self.tabs.addTab(self._route_tab(), "Route")
        self.map = MapView()
        self.tabs.addTab(self.map, "Map")

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self.stale)
        layout.addWidget(self.tabs)
        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)

        self._label_units()
        self.units.currentIndexChanged.connect(self.change_units)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)
        self.poll()

    @staticmethod
    def _table(headers) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    WEIGHT_LABELS = {
        "contracts": ("Contracts completed", "Whole contracts delivered on time."),
        "money": ("Money (NOK per km)", "What the route earns, less what sailing costs."),
        "fill": ("Hold usage", "How full the hold is on the legs sailed."),
        "empty": ("Empty legs (a penalty)", "The share of the distance sailed with nothing aboard."),
        "stranded": ("Stranded cargo (a penalty)", "Cargo the route leaves behind."),
        "late": ("Deadlines (a penalty, top priority)", "Contracts that would arrive after their deadline. "
                                                          "Keep this the largest weight."),
    }

    def _weights_controls(self):
        """The weights of the weighted score, with presets; shown only when that priority is chosen."""
        self.weights_box = QWidget()
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.weight_preset = QComboBox()
        self.weight_preset.addItem("Custom", None)
        for name in PRESETS:
            self.weight_preset.addItem(name.capitalize(), name)
        form.addRow("Weights", self.weight_preset)
        self.weight_spins = {}
        start = Weights()
        for key, (label, tip) in self.WEIGHT_LABELS.items():
            box = QDoubleSpinBox(minimum=0.0, maximum=100.0, decimals=1, singleStep=0.5)
            box.setToolTip(tip)
            box.setValue(float(self.settings.value(f"weight_{key}", getattr(start, key))))
            self.weight_spins[key] = box
            form.addRow(label, box)
        self.weights_box.setLayout(form)
        self.weight_preset.currentIndexChanged.connect(self._apply_weight_preset)

    def _apply_weight_preset(self):
        preset = PRESETS.get(self.weight_preset.currentData() or "")
        if not preset:
            return
        for key, value in preset.as_dict().items():
            self.weight_spins[key].setValue(value)  # each change replans, after a moment

    def _show_weights(self):
        self.weights_box.setVisible(self.priority.currentData() == "custom")

    def weights(self) -> Weights:
        return Weights(**{key: box.value() for key, box in self.weight_spins.items()})

    def _route_tab(self) -> QWidget:
        self.start_port = QComboBox()
        self.priority = QComboBox()
        for key, label in PRIORITIES.items():
            self.priority.addItem(label, key)
        self.priority.setCurrentIndex(max(0, self.priority.findData(self.settings.value("priority", "full"))))
        self._weights_controls()
        self.max_weight = QDoubleSpinBox(maximum=1_000_000, decimals=0, singleStep=100, suffix=" kg", specialValueText="No limit")
        self.max_weight.setValue(float(self.settings.value("maxWeight", 0)))
        self.max_items = QSpinBox(maximum=10_000, specialValueText="No limit")
        self.max_items.setValue(int(self.settings.value("maxItems", 0)))
        self.max_stops = QSpinBox(minimum=1, maximum=60)
        self.max_stops.setValue(int(self.settings.value("maxStops", 12)))

        self.use_hold = QCheckBox("Only load what fits the hold")
        self.use_hold.setChecked(self.settings.value("useHold", "true") == "true")
        self.stacking = QCheckBox("Cargo can be stacked")
        self.stacking.setChecked(self.settings.value("stacking", "true") == "true")
        self.no_splits = QCheckBox("Never split a contract's cargo")
        self.no_splits.setToolTip("Only load a contract when all of its cargo waiting at that port fits.")
        self.no_splits.setChecked(self.settings.value("noSplits", "false") == "true")
        self.highlight = QCheckBox("Highlight this port's cargo in game")
        self.highlight.setToolTip("In the game's warehouse window and crane cargo list: green for cargo to load "
                                  "or take out, orange for cargo to unload from the boat.")
        self.highlight.setChecked(self.settings.value("highlight", "true") == "true")
        self.crane_orders = QCheckBox("Send orders to the crane operator")
        self.crane_orders.setToolTip("With the SasCraneOperator mod, the boat's crane operator loads and unloads "
                                     "this port's cargo as planned. Off, they only move cargo you mark yourself.")
        self.crane_orders.setChecked(self.settings.value("craneOrders", "true") == "true")

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.addRow("Start port", self.start_port)
        form.addRow("Prioritise", self.priority)
        form.addRow(self.weights_box)
        form.addRow("Boat weight limit", self.max_weight)
        form.addRow("Boat item limit", self.max_items)
        form.addRow("Stops to plan", self.max_stops)
        form.addRow(self.use_hold)
        form.addRow(self.stacking)
        form.addRow(self.no_splits)
        form.addRow(self.highlight)
        form.addRow(self.crane_orders)
        self.plan_summary = QLabel()
        self.plan_summary.setWordWrap(True)
        audit_button = QPushButton("Open the audit log")
        audit_button.setToolTip("Every route the planner works out, what made it, and every change in the orders "
                                "sent to the crane operator, one line each.")
        audit_button.clicked.connect(self.open_audit)
        replan_button = QPushButton("Replan from here")
        replan_button.setToolTip("Throw the current route away and plan a new one from the latest snapshot.")
        replan_button.clicked.connect(self.replan_by_hand)
        check_button = QPushButton("Check load now")
        check_button.setToolTip("Asks the game for a fresh snapshot and compares the boat with this stop's instructions.")
        check_button.clicked.connect(self.request_snapshot)
        self.load_check = QLabel()
        self.load_check.setWordWrap(True)
        side = QVBoxLayout()
        side.addLayout(form)
        side.addWidget(check_button)
        side.addWidget(self.load_check)
        side.addWidget(replan_button)
        side.addWidget(audit_button)
        side.addWidget(self.plan_summary)
        side.addStretch(1)

        # Planning takes a moment, so wait for the controls to settle before running it.
        self.replan_timer = QTimer(self, singleShot=True, interval=350)
        self.replan_timer.timeout.connect(self.replan)

        self.plan_tree = QTreeWidget()
        self.plan_tree.setHeaderLabels(["Stop", "Items", "kg", "Money", "Leg km"])
        header = self.plan_tree.header()
        header.setSectionResizeMode(QHeaderView.Interactive)  # every column can be dragged wider or narrower
        header.setStretchLastSection(True)
        self.plan_tree.setColumnWidth(0, 620)
        for column in (1, 2, 3):
            self.plan_tree.setColumnWidth(column, 80)
        saved = self.settings.value("routeColumns")
        if saved:
            header.restoreState(saved)
        header.sectionResized.connect(lambda *_: self.settings.setValue("routeColumns", header.saveState()))
        self.plan_tree.setUniformRowHeights(False)  # instructions span several lines

        for widget, signal in ((self.start_port, "currentIndexChanged"), (self.priority, "currentIndexChanged"),
                               (self.max_weight, "valueChanged"), (self.max_items, "valueChanged"),
                               (self.max_stops, "valueChanged"), (self.use_hold, "toggled"),
                               (self.stacking, "toggled"), (self.no_splits, "toggled"),
                               (self.highlight, "toggled"), (self.crane_orders, "toggled")):
            getattr(widget, signal).connect(lambda *_: self.replan_timer.start())
        for box in self.weight_spins.values():
            box.valueChanged.connect(lambda *_: self.replan_timer.start())
        self.priority.currentIndexChanged.connect(self._show_weights)
        self._show_weights()

        panel = QWidget()
        panel.setLayout(side)
        panel.setFixedWidth(330)
        # The controls are tall: they scroll, so that the window can be made as small as you like.
        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(330 + scroll.verticalScrollBar().sizeHint().width() + 4)
        self.route_controls = scroll
        layout = QHBoxLayout()
        self.busy = QWidget()
        busy_layout = QHBoxLayout()
        busy_layout.setContentsMargins(0, 0, 0, 0)
        busy_layout.addWidget(QLabel("Calculating the route…"))
        bar = QProgressBar()
        bar.setRange(0, 0)  # no known end: it just moves
        bar.setTextVisible(False)
        busy_layout.addWidget(bar, 1)
        self.busy.setLayout(busy_layout)
        self.busy.hide()
        right = QVBoxLayout()
        right.addWidget(self.busy)
        right.addWidget(self.plan_tree, 1)
        layout.addWidget(scroll)
        layout.addLayout(right, 1)
        tab = QWidget()
        tab.setLayout(layout)
        return tab

    # ------------------------------------------------------------------------------------------

    @property
    def unit(self) -> str:
        return self.units.currentData()

    def distance(self, km: float | None, digits: int = 1) -> str:
        """A distance held in km, written in the chosen unit; "?" when it is not known."""
        return "?" if km is None else f"{km / DISTANCE_UNITS[self.unit][1]:.{digits}f}"

    def _label_units(self):
        unit = self.unit
        self.matrix.setHorizontalHeaderItem(6, QTableWidgetItem(unit))
        self.aboard.setHorizontalHeaderItem(6, QTableWidgetItem(f"{unit} from here"))
        self.plan_tree.setHeaderLabels(["Stop", "Items", "kg", "Money", f"Leg {unit}"])
        self.priority.setItemText(self.priority.findData("money"), f"Most money per {unit}")

    def change_units(self):
        """Redraws every distance in the new unit. The route itself is left alone."""
        self.settings.setValue("distanceUnit", self.unit)
        self._label_units()
        self._fill_matrix()
        self._fill_aboard()
        if self.committed:
            self.follow()

    def browse(self):
        chosen, _ = QFileDialog.getOpenFileName(self, "Snapshot written by the SasLogistics mod", self.path, "snapshot.json (*.json)")
        if chosen:
            self.path = chosen
            self.settings.setValue("snapshotPath", chosen)
            self.mtime = None
            self.poll()

    def update_sea_routes(self):
        """Keeps sea distances in step with the depth grid the mod wrote and the ports in the snapshot.

        Routes come from a saved file when one matches; otherwise they are worked out on a background
        thread, which takes a few seconds, and straight lines are used until that finishes.
        """
        grid_path = Path(self.path).with_name("depthgrid.txt")
        ports = {p.id: p.location[:2] for p in self.snap.ports.values() if p.location and any(p.location[:2])}
        job = self.sea_job
        if job and job["done"]:
            self.sea_job = None
            if job["routes"] is not None:
                searoutes.save_cache(grid_path.with_name("searoutes.json"), job["signature"], job["routes"])
                self._use_sea_routes(job["routes"], job["source"])
                if self.committed:
                    self.sea_note += (" The route on screen was planned with straight lines; press Replan from "
                                      "here to plan it again by sea.")
                self.refresh()
            return
        if job:
            self.sea_note = f"Distances are straight lines for now: working out sea routes ({job['at']} of {job['of']} ports)."
            return
        try:
            source = (grid_path.stat().st_mtime, tuple(sorted(ports.items())))
        except OSError:
            self.sea_note = ("Distances are straight lines between ports. Sea routes need the depth map, which the "
                             "mod writes the first time a save is loaded.")
            return
        if not ports or source == self.sea_source:
            return
        grid = searoutes.load_depth_grid(grid_path)
        if grid is None:
            return  # still being written
        self.map.set_grid(grid)
        wanted = searoutes.signature(grid, ports)
        cached = searoutes.load_cache(grid_path.with_name("searoutes.json"), wanted)
        if cached is not None:
            self._use_sea_routes(cached, source)
            return
        job = {"done": False, "routes": None, "signature": wanted, "source": source, "at": 0, "of": len(ports)}

        def work():
            try:
                job["routes"] = searoutes.compute_routes(grid, ports, lambda at, of: job.update(at=at, of=of))
            finally:
                job["done"] = True

        self.sea_job = job
        self.sea_note = "Distances are straight lines for now: working out sea routes from the game's depth map."
        threading.Thread(target=work, daemon=True).start()

    def _use_sea_routes(self, routes, source):
        self.sea_km = searoutes.distances(routes)
        self.sea_routes = routes
        self.sea_source = source
        self.snap.sea_km = self.sea_km
        self.sea_note = (f"Distances follow the water: {len(routes)} sea routes worked out from the game's depth map. "
                         "Ports with no route between them fall back to a straight line.")

    def poll(self):
        self.update_sea_routes()
        # Cargo the crane operator could not fit into the hold: plan without it, and plan again when
        # that changes (it is cleared once the hold has changed and the pieces may fit).
        reported = read_no_room(self.path)
        if reported != self.no_room:
            self.audit.record("no room reported", pieces=sorted(reported))
            self.no_room = reported
            if self.committed is not None:
                self.plan_cause = "the crane operator reported cargo that does not fit the hold"
                self.replan_timer.start()
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            self.status.setText(f"No snapshot yet. Press F8 in game.  ({self.path})")
            self.stale.hide()
            return
        warning = stale_warning(time.time() - mtime)
        self.stale.setText(warning)
        self.stale.setVisible(bool(warning))
        if mtime == self.mtime:
            return
        try:
            self.snap = load_snapshot(self.path)
            self.snap.sea_km = self.sea_km
        except (OSError, ValueError) as error:  # the mod may be mid-write; try again next tick
            self.status.setText(f"Could not read snapshot: {error}")
            return
        self.mtime = mtime
        self.update_sea_routes()  # the first snapshot is what tells us where the ports are
        self.refresh()

    def refresh(self):
        snap = self.snap
        open_items = snap.undelivered()
        lost = sum(1 for i in open_items if i.location is None)
        text = (f"Snapshot {snap.time.replace('T', ' ')}  ·  {len(snap.missions)} cargo contracts  ·  "
                f"{len(open_items)} items to deliver  ·  {money(sum(i.value for i in open_items))} still to earn")
        docked = sum(1 for i in open_items if i.on_dock)
        if docked:
            text += f"  ·  {docked} On the Dock"
        if lost:
            text += f"  ·  {lost} items not found in any warehouse, dock or boat"
        if snap.errors:
            text += f"  ·  {len(snap.errors)} mod read errors"
        self.status.setText(text)
        self._fill_contracts()
        self._fill_matrix()
        self._fill_aboard()
        self._update_transactions()

        wanted = self.start_port.currentData() if self.start_port.currentIndex() > 0 else None
        self.start_port.blockSignals(True)
        self.start_port.clear()
        current = snap.here()
        self.start_port.addItem(f"Where I am ({snap.port_name(current)})" if current else "Where I am (unknown)", None)
        for port in sorted(snap.ports.values(), key=lambda p: p.name):
            self.start_port.addItem(port.name, port.id)
        if wanted:
            self.start_port.setCurrentIndex(max(0, self.start_port.findData(wanted)))
        self.start_port.blockSignals(False)
        self.follow()

    # ------------------------------------------------------------------------------------------ transactions

    def _transactions_tab(self) -> QWidget:
        self.tx_summary = QLabel()
        self.tx_summary.setWordWrap(True)
        self.tx_note = QLabel()
        self.tx_note.setWordWrap(True)
        self.tx_note.setStyleSheet("color: #e5484d;")
        self.tx_note.hide()
        self.tx_filter = QLineEdit()
        self.tx_filter.setPlaceholderText("Filter by description, type, date or amount…")
        self.tx_filter.setClearButtonEnabled(True)
        self.tx_filter.textChanged.connect(lambda *_: self._fill_transactions())
        export = QPushButton("Export CSV…")
        export.setToolTip("Saves the transactions shown (all of them when nothing is typed in the filter) with exact "
                          "amounts, for a spreadsheet.")
        export.clicked.connect(self.export_transactions)
        clear = QPushButton("Clear saved history")
        clear.setToolTip("Forgets the history saved by the planner. The game's own recent list fills it again.")
        clear.clicked.connect(self.clear_transactions)
        bar = QHBoxLayout()
        bar.addWidget(self.tx_filter, 1)
        bar.addWidget(export)
        bar.addWidget(clear)
        self.transactions = self._table(["Date", "Description", "Type", "Amount", "Balance after"])
        layout = QVBoxLayout()
        layout.addWidget(self.tx_summary)
        layout.addWidget(self.tx_note)
        layout.addLayout(bar)
        layout.addWidget(self.transactions, 1)
        tab = QWidget()
        tab.setLayout(layout)
        return tab

    def _update_transactions(self):
        added = self.ledger.merge(self.snap.finance)
        self._fill_transactions()
        return added

    def _shown_transactions(self):
        """The ledger's rows, newest first, that pass the filter."""
        needle = self.tx_filter.text().strip().lower()
        unit = self.ledger.unit
        rows = self.ledger.rows()
        if not needle:
            return rows
        return [(e, b) for e, b in rows
                if needle in e.description.lower() or needle in e.date or needle in kind(e).lower()
                or needle in exact(e.raw, unit)]

    def _fill_transactions(self):
        ledger, unit = self.ledger, self.ledger.unit
        shown = self._shown_transactions()
        rows = []
        for entry, balance in shown:
            rows.append([QTableWidgetItem(entry.date), QTableWidgetItem(entry.description), QTableWidgetItem(kind(entry)),
                         NumberItem(entry.raw / unit, exact(entry.raw, unit, group=True)),
                         NumberItem(0 if balance is None else balance / unit,
                                    "" if balance is None else exact(balance, unit, group=True))])
        self._fill(self.transactions, rows)
        income, spent, net = ledger.totals([e for e, _ in shown])
        text = f"{len(shown)} of {len(ledger.entries)} transactions"
        if shown:
            text += f"  ·  in {exact(income, unit, True)}  ·  out {exact(-spent, unit, True)}  ·  net {exact(net, unit, True)}"
        if ledger.capital_raw is not None:
            text += f"  ·  capital now {exact(ledger.capital_raw, unit, True)}"
        if not ledger.entries:
            text = ("No transactions yet. The SasLogistics mod writes the game's ledger into each snapshot (press F8 in "
                    "game); it needs the version of the mod that has this.")
        self.tx_summary.setText(text)
        self.tx_note.setText(ledger.note)
        self.tx_note.setVisible(bool(ledger.note))

    def export_transactions(self):
        shown = self._shown_transactions()
        if not shown:
            QMessageBox.information(self, "Export transactions", "There are no transactions to export.")
            return
        start = str(Path.home() / "Documents" / "ships-at-sea-transactions.csv")
        chosen, _ = QFileDialog.getSaveFileName(self, "Export transactions", start, "CSV files (*.csv)")
        if not chosen:
            return
        try:
            count = write_csv(chosen, list(reversed(shown)), self.ledger.unit)  # oldest first, for a spreadsheet
        except OSError as error:
            QMessageBox.warning(self, "Export transactions", f"Could not write the file: {error}")
            return
        self.status.setText(f"Exported {count} transactions to {chosen}")

    def clear_transactions(self):
        if QMessageBox.question(self, "Clear saved history",
                                "Forget the transactions saved by the planner? The game's own recent list will fill "
                                "it again; older ones will be gone.") == QMessageBox.Yes:
            self.ledger.clear()
            self._update_transactions()

    def _fill(self, table, rows):
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                table.setItem(r, c, cell if isinstance(cell, QTableWidgetItem) else QTableWidgetItem(str(cell)))
        table.setSortingEnabled(True)

    def _fill_contracts(self):
        snap, rows = self.snap, []
        for mission in snap.missions:
            left = [i for i in mission.items if not i.delivered]
            places = Counter(snap.place_name(i) for i in left)
            rows.append([
                mission.short_id, mission.title, snap.port_name(mission.start), snap.port_name(mission.end),
                NumberItem(len(mission.items) - len(left), f"{len(mission.items) - len(left)} / {len(mission.items)}"),
                ", ".join(f"{place} ×{n}" for place, n in places.most_common()) or "—",
                NumberItem(sum(i.weight for i in left)), NumberItem(mission.remaining_value),
                "Ready to complete: take out of warehouse" if mission.to_take_out else mission.status,
            ])
        self._fill(self.contracts, rows)

    def _write_highlights(self, plan):
        """Tells the mods which cargo to move at the port the player is in: what to load, and, marked
        with a U, what still has to come off the boat. highlight.txt is shown in the game's windows and
        crane_orders.txt is worked by the crane operator; each is left empty while its box is off.
        Cargo to take out of the warehouse to finish a contract is highlighted but not ordered.
        Cargo the route only leaves in the warehouse (not delivered here, put off at the nearest stop), and
        delivered pieces left in the hold, are neither marked nor ordered: a mark in the game means the
        operator will move that piece, so a piece she will not move must not carry one. The ones the route
        leaves are listed in removable.txt instead, for the operator to take off only if she needs the room
        for a load. The route summary lists them."""
        wanted, orders, removable = [], [], []
        first = plan.stops[0] if plan.stops else None
        if first and first.port == self.snap.docked_port():
            removable = [i.guid for i in first.drop]  # the operator may take these off, only if she needs the room
            wanted = [i.guid for i in first.load] + [i.guid for i, _ in first.stage]
            wanted += [f"{i.guid} U" for i in first.unload]
            orders = list(wanted)
            # The stored part of a contract being finished, and the cargo to take out of the warehouse to
            # finish one, wherever the boat is docked, is only highlighted for the player to take out. It
            # is not ordered: a plain line in crane_orders.txt means "load this onto the boat", and the
            # operator would carry the pieces the player puts on the dock for the delivery back aboard.
            for _, stored in completing_unloads(self.snap, first):
                wanted += [i.guid for i in stored]
        for mission in self.snap.completable_at(self.snap.docked_port()):
            wanted += [i.guid for i in mission.to_take_out]
        names = {i.guid: i.name for i in self.snap.items}
        label = lambda lines: [f"{line.split()[0][:8]} {names.get(line.split()[0], '?')}" + (" UNLOAD" if " U" in line else "")
                               for line in lines]  # noqa: E731
        self.audit.record_if_changed(
            "orders", port=first.port if first else None, docked=self.snap.docked_port(),
            highlight=label(wanted), crane_orders=label(orders) if self.crane_orders.isChecked() else "off",
            removable=label(removable) if self.crane_orders.isChecked() else "off")
        for name, on, guids in (("highlight.txt", self.highlight.isChecked(), wanted),
                                ("crane_orders.txt", self.crane_orders.isChecked(), orders),
                                ("removable.txt", self.crane_orders.isChecked(), removable)):
            target = Path(self.path).with_name(name)
            try:
                if target.parent.is_dir():
                    target.write_text("".join(guid + "\n" for guid in guids) if on else "", encoding="ascii")
            except OSError:
                pass  # a nicety; the plan on screen is still right

    def _fill_aboard(self):
        snap, rows = self.snap, []
        here = snap.here()
        for held in snap.aboard:
            cargo = held.cargo
            if cargo:
                km = snap.distance_km(here, cargo.dest)
                rows.append([held.name, cargo.mission.short_id, cargo.mission.title, snap.port_name(cargo.dest),
                             NumberItem(held.weight), NumberItem(cargo.value),
                             NumberItem(km if km is not None else -1, self.distance(km))])
            else:
                rows.append([held.name, "", "Not contract cargo", "", NumberItem(held.weight), NumberItem(0, ""), NumberItem(-1, "")])
        self._fill(self.aboard, rows)
        self.aboard.sortItems(3)
        self.tabs.setTabText(self.tabs.indexOf(self.aboard.parentWidget()), f"On board ({len(rows)})")

        boat = snap.boat_name or "No boat found"
        text = f"{boat}  ·  {len(rows)} items  ·  {money(sum(h.weight for h in snap.aboard))} kg"
        if snap.hold_size:
            cells = snap.hold_size[0] * snap.hold_size[1] * snap.hold_size[2]
            text += f"  ·  hold {snap.hold_cells_used / cells:.0%} full"
        dests = Counter(snap.port_name(h.cargo.dest) for h in snap.aboard if h.cargo)
        if dests:
            text += "  ·  for " + ", ".join(f"{name} ×{n}" for name, n in dests.most_common())
        self.aboard_summary.setText(text)

    def _fill_matrix(self):
        snap = self.snap
        groups = defaultdict(list)
        for item in snap.undelivered():
            groups[(item.location, item.on_dock, item.dest)].append(item)
        rows = []
        for (place, on_dock, dest), items in groups.items():
            km = snap.distance_km(place, dest)
            where = "On the Dock" if on_dock else "Warehouse" if place in snap.ports else ""
            rows.append([
                snap.port_name(place), where, snap.port_name(dest), NumberItem(len(items)),
                NumberItem(sum(i.weight for i in items)), NumberItem(sum(i.value for i in items)),
                NumberItem(km if km is not None else -1, self.distance(km)),
            ])
        self._fill(self.matrix, rows)
        self.matrix.sortItems(0)

    def open_audit(self):
        path = self.audit.path
        if not path or not path.is_file():
            QMessageBox.information(self, "Audit log", "Nothing has been logged yet.")
            return
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # noqa: S606
        else:
            self.status.setText(f"Audit log: {path}")

    def replan_by_hand(self):
        """Replan from here: the route is the player's own asking, so the note about a route that changed by
        itself no longer applies."""
        self.replan_reason = ""
        self.plan_cause = "Replan from here was pressed"
        self.replan()

    def replan(self):
        for key, widget in (("maxWeight", self.max_weight), ("maxItems", self.max_items), ("maxStops", self.max_stops)):
            self.settings.setValue(key, widget.value())
        self.settings.setValue("priority", self.priority.currentData())
        for key, box in self.weight_spins.items():
            self.settings.setValue(f"weight_{key}", box.value())
        self.settings.setValue("useHold", "true" if self.use_hold.isChecked() else "false")
        self.settings.setValue("stacking", "true" if self.stacking.isChecked() else "false")
        self.settings.setValue("noSplits", "true" if self.no_splits.isChecked() else "false")
        self.settings.setValue("highlight", "true" if self.highlight.isChecked() else "false")
        self.settings.setValue("craneOrders", "true" if self.crane_orders.isChecked() else "false")
        snap = self.snap
        start = self.start_port.currentData() or snap.here()
        if not start:
            self.plan_tree.clear()
            self.plan_summary.setText("Pick a start port: the snapshot has no player or port positions.")
            return
        if self.planning:  # one at a time: this one starts when the run under way is done
            self.plan_again = True
            return
        hold = None
        if self.use_hold.isChecked() and snap.hold_size:
            hold = Hold(snap.hold_size, snap.hold_sizes, self.stacking.isChecked())
        capacity = Capacity(self.max_weight.value(), self.max_items.value(), hold,
                            allow_splits=not self.no_splits.isChecked())
        # The planner thread gets its own copy of the snapshot's fields and its own hold, so nothing
        # the window changes meanwhile can reach it.
        self.plan_job = {"snap": copy.copy(snap), "start": start, "capacity": capacity,
                         "priority": self.priority.currentData(), "max_stops": self.max_stops.value(),
                         "no_room": self.no_room, "weights": self.weights()}
        self.plan_job["cause"] = self.plan_cause or "a setting or the start port changed"
        self.plan_cause = ""
        self.plan_job["settings"] = {
            "priority": self.priority.currentData(), "max_stops": self.max_stops.value(),
            "max_weight": self.max_weight.value(), "max_items": self.max_items.value(),
            "use_hold": self.use_hold.isChecked(), "stacking": self.stacking.isChecked(),
            "no_splits": self.no_splits.isChecked(), "weights": self.weights().as_dict() if self.weights() else None}
        self.planning = True
        self.busy.show()
        self.plan_tree.setEnabled(False)  # the plan on screen is the old one: greyed until the new one is in
        self.runner.run(self.plan_job)

    def _planned(self, plan, error):
        """The planner thread has finished (this runs on the window's thread)."""
        job, self.plan_job, self.planning = self.plan_job, None, False
        if self.plan_again:  # settings changed while it ran: this plan is out of date
            self.plan_again = False
            self.replan()
            return
        self.busy.hide()
        self.plan_tree.setEnabled(True)
        if error is not None:
            self.plan_summary.setText(f"Planning failed: {error}")
            return
        snap = job["snap"]
        self.audit.record(
            "plan", cause=job.get("cause", ""), snapshot=snap.time, start=job["start"], boat=snap.boat_name,
            docked=snap.docked_port(), aboard=len([i for i in snap.items if i.location == BOAT]),
            settings=job.get("settings"), no_room=sorted(job.get("no_room") or []), summary=counts_of(plan),
            **plan_of(plan, snap))
        self.committed = plan
        self.committed_at = snap.time[11:16]
        self.sea_note = self.sea_note.split(" The route on screen")[0]
        self.known_cargo = {item.guid for item in snap.items}
        self.show_plan(self.committed)

    def request_snapshot(self):
        """Asks the mod to write a snapshot now, so a check does not wait for the next automatic one."""
        try:
            Path(self.path).with_name("request.txt").write_text("snapshot\n", encoding="ascii")
            self.load_check.setText("Asked the game for a fresh snapshot…")
        except OSError as error:
            self.load_check.setText(f"Could not ask the game for a snapshot: {error}")

    def _load_check_lines(self):
        """The boat compared with the current stop's instructions, as (headline, detail lines)."""
        plan = self.committed
        if not plan or not plan.stops:
            return "", []
        stop, snap = plan.stops[0], self.snap
        if snap.docked_port() != stop.port:
            if snap.docked_port() is None:
                # Under way: the next stop is the one after where the plan currently stands.
                heading = plan.stops[1].port if len(plan.stops) > 1 else None
                return "In transit" + (f" to {snap.port_name(heading)}." if heading else "."), []
            return f"Load check: not docked at {snap.port_name(stop.port)}.", []
        check = check_load(plan, snap)
        finishing = snap.completable_at(stop.port)
        if check.ready and not finishing:
            done = f"all {check.planned} loaded" if check.planned else "nothing to load here"
            return f"Load check: {done}, nothing left to unload. Ready to leave.", []
        parts, lines = [], []
        if finishing:
            parts.append(f"{len(finishing)} contract{'' if len(finishing) == 1 else 's'} to complete from the warehouse")
        if check.planned:
            parts.append(f"{check.loaded} of {check.planned} loaded")
        for label, items in (("still to load", check.to_load), ("still to unload", check.to_unload),
                             ("on board but not in the plan", check.extra)):
            if items:
                parts.append(f"{len(items)} {label}")
                by_mission = defaultdict(list)
                for item in items:
                    by_mission[item.mission.label].append(item)
                for mission_label, group in sorted(by_mission.items()):
                    lines.append(f"{label.capitalize()}: {mission_label}\n{summarise(group)}")
        return "Load check: " + ", ".join(parts) + ".", lines

    def follow(self):
        """A new snapshot arrived. Keep the route being followed and only tick off what has been done;
        plan afresh only when there is no route, it is finished, or the boat docked somewhere off it."""
        plan = advance(self.committed, self.snap.docked_port()) if self.committed else None
        view = remaining(plan, self.snap) if plan else None
        if view is None or not has_work(view):
            # Said in the route summary and the window's status line, so that a route that changes by itself is
            # never a surprise: what made it, and when.
            if not self.committed:
                self.replan_reason = "there was no route yet"
            elif plan is None:
                self.replan_reason = (f"the boat is docked at {self.snap.port_name(self.snap.docked_port())}, "
                                      "which the route did not go to")
            else:
                self.replan_reason = "all the work the route had left was done"
            self.replan_reason_at = time.strftime("%H:%M:%S")
            self.plan_cause = "automatic: " + self.replan_reason
            self.replan()
            return
        # Cargo that came aboard after the route was planned has no line in it. What is for the port the boat is
        # in is added to the unloads at once, so that it is shown and sent to the crane operator. The route itself
        # is never changed behind your back: a new route replaces the orders the operator is carrying out, and
        # pieces would be moved back and forth. The rest is pointed out, and Replan from here includes it.
        view = with_unplanned_unloads(view, self.snap, self.snap.docked_port())
        self.strays = sorted(unplanned_aboard(view, self.snap), key=lambda i: i.name)
        self.audit.record_if_changed("aboard but not in the route", pieces=items_of(self.strays))
        self.committed = plan
        self.show_plan(view)

    def show_plan(self, plan):
        snap = self.snap
        self.map.set_state(snap, plan, self.sea_routes)
        self.plan_tree.clear()
        waiting = waiting_by_contract(snap, plan)
        self._write_highlights(plan)

        def load_row(node, stop_index, heading, group, earned):
            """A load line, flagged when it takes only some of that contract's cargo waiting at the port."""
            mission, here = group[0].mission, waiting[stop_index].get(group[0].mission.id, len(group))
            part = f"  [PARTIAL: {len(group)} of {here} here]" if len(group) < here else ""
            docked = [i for i in group if i.on_dock]
            if docked:  # already out of the warehouse, so there is nothing to fetch for these
                part += "  [On the Dock]" if len(docked) == len(group) else f"  [{len(docked)} On the Dock: {summarise(docked, 10_000)}]"
            row = QTreeWidgetItem(node, [f"{heading}: {mission.label}{part}\n{summarise(group)}", str(len(group)),
                                         money(sum(i.weight for i in group)), earned, ""])
            if "PARTIAL" in part:
                font = row.font(0)
                font.setBold(True)
                row.setFont(0, font)

        for number, stop in enumerate(plan.stops, 1):
            title = f"{number}. {snap.port_name(stop.port)}"
            if stop.fill is not None:
                title += f"  (leaves {stop.fill:.0%} full)"
            node = QTreeWidgetItem(self.plan_tree, [
                title, str(stop.onboard_count), money(stop.onboard_weight), money(stop.earned),
                self.distance(stop.leg_km),
            ])
            if number == 1 and self.strays:
                row = QTreeWidgetItem(node, [
                    f"Aboard, but not in this route (loaded since it was made): press Replan from here to include them\n"
                    f"{summarise(self.strays)}", str(len(self.strays)), money(sum(i.weight for i in self.strays)), "", ""])
                font = row.font(0)
                font.setBold(True)
                row.setFont(0, font)
            if number == 1:
                headline, details = self._load_check_lines()
                self.load_check.setText(headline + ("" if not details else "\n\n" + "\n\n".join(details)))
                if headline:
                    row = QTreeWidgetItem(node, [headline, "", "", "", ""])
                    font = row.font(0)
                    font.setBold(True)
                    row.setFont(0, font)
            # A whole contract sitting in this port's warehouse only needs taking out to be finished.
            for mission in sorted(snap.completable_at(stop.port), key=lambda m: m.label):
                cargo = mission.to_take_out
                row = QTreeWidgetItem(node, [
                    f"Take out of the warehouse to complete: {mission.label}\n{summarise(cargo)}",
                    str(len(cargo)), money(sum(i.weight for i in cargo)), "", ""])
                font = row.font(0)
                font.setBold(True)
                row.setFont(0, font)
            # The boat brings the last of a contract whose other cargo is already in this warehouse.
            for mission, stored in sorted(completing_unloads(snap, stop), key=lambda pair: pair[0].label):
                row = QTreeWidgetItem(node, [
                    f"Once unloaded, also take out of the warehouse to complete: {mission.label}\n{summarise(stored)}",
                    str(len(stored)), money(sum(i.weight for i in stored)), "", ""])
                font = row.font(0)
                font.setBold(True)
                row.setFont(0, font)
            sections = [("Unload", stop.unload), ("Hand in from storage", stop.hand_in),
                        ("Leave in warehouse", stop.drop)]
            if stop.unload_all and stop.unload:  # once it has all come off there is nothing left to say
                # The whole boat comes off here, so there is nothing to pick out; warehouse drops are still listed.
                everything = stop.unload + stop.drop
                QTreeWidgetItem(node, ["Unload All", str(len(everything)), money(sum(i.weight for i in everything)),
                                       money(sum(i.value for i in stop.unload)), ""])
                sections = sections[1:]
            for label, items in sections:
                by_mission = defaultdict(list)
                for item in items:
                    by_mission[item.mission.label].append(item)
                for mission_label, group in sorted(by_mission.items()):
                    earned = "" if items is stop.drop else money(sum(i.value for i in group))  # a drop pays nothing yet
                    QTreeWidgetItem(node, [f"{label}: {mission_label}\n{summarise(group)}", str(len(group)),
                                           money(sum(i.weight for i in group)), earned, ""])
            by_contract = defaultdict(list)
            for item in stop.load:
                by_contract[(snap.port_name(item.dest), item.mission.label)].append(item)
            for (dest_name, _), group in sorted(by_contract.items()):
                load_row(node, number - 1, f"Load for {dest_name}", group, money(sum(i.value for i in group)))
            by_drop = defaultdict(list)
            for item, leave_at in stop.stage:
                by_drop[(snap.port_name(leave_at), item.mission.label)].append(item)
            for (leave_name, _), group in sorted(by_drop.items()):
                load_row(node, number - 1, f"Load to move closer, leave at {leave_name}", group, "")
            node.setExpanded(True)

        on_boat = sum(1 for i in snap.undelivered() if i.location == BOAT)
        lines = [f"{len(plan.stops)} stops, {plan.delivered} items delivered, {money(plan.total_money)} earned, "
                 f"{self.distance(plan.total_km, 0)} {self.unit}{' (some legs unknown)' if plan.has_unknown_distance else ''}."]
        lines.append(f"Following the route planned at {self.committed_at}. It stays put as you work through it and "
                     "is only replanned when you press Replan, change a setting, finish it, or dock at a port it "
                     "does not visit.")
        new_cargo = sum(1 for i in snap.undelivered() if i.guid not in self.known_cargo)
        if new_cargo:
            lines.append(f"{new_cargo} cargo items have appeared since then and are not in this route. Replan to include them.")
        if plan.dropped:
            lines.append(f"Cargo already on board that this route does not deliver: {plan.dropped}. It is left in the "
                         "warehouse at the stop closest to its destination. If the crane operator cannot make room for a load any "
                         "other way, she takes such cargo off at that stop, the biggest first, and only as much as she needs.")
        if snap.unmarked:
            lines.append(f"{len(snap.unmarked)} piece(s) the game lists as delivered are not at their destination "
                         f"({', '.join(sorted({i.name for i in snap.unmarked}))}), so they are planned as cargo still to deliver.")
        if snap.delivered_aboard:
            cells = sum(len(i.placed or []) for i in snap.delivered_aboard)
            names = ", ".join(sorted({i.name for i in snap.delivered_aboard}))
            lines.append(f"{len(snap.delivered_aboard)} delivered piece(s) are still in the hold ({names}), taking "
                         f"{cells} of its cells. The route cannot use that room until they are taken off.")
        left_out = sum(1 for i in snap.undelivered() if i.guid.upper() in self.no_room and i.location != BOAT)
        if left_out:
            lines.append(f"{left_out} items the crane operator could not fit into the hold are left out of this route. "
                         "It plans for them again once the hold changes.")
        if plan.staged:
            lines.append(f"Spare space moves {plan.staged} more items closer to their destination for a later trip.")
        if plan.left_behind:
            lines.append(f"{plan.left_behind} items are not covered within this many stops.")
        if on_boat:
            lines.append(f"{on_boat} items are already on a boat.")
        if snap.hold_size:
            x, y, z = snap.hold_size
            cm = snap.cell_cm
            metres = f", {x * cm / 100:g} × {y * cm / 100:g} × {z * cm / 100:g} m" if cm else ""
            lines.append(f"Hold: {x} × {y} × {z} cells{metres}.")
        else:
            lines.append("No hold found for your boat in the snapshot, so space is not checked.")
        lines.append(self.sea_note)
        if self.replan_reason:
            lines.append(f"This route was worked out again by itself at {self.replan_reason_at} because "
                         f"{self.replan_reason}.")
        lines.append("Item counts in the Stop rows are what is on board when leaving.")
        self.plan_summary.setText("\n\n".join(lines))
