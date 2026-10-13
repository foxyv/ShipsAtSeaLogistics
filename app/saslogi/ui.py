"""PySide6 window: contracts (the spreadsheet replacement), cargo by port, and the route planner."""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
import threading
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from . import searoutes
from .mapview import MapView
from .model import AT_SEA, BOAT, Snapshot, load_snapshot
from .packing import Hold
from .planner import (
    PRIORITIES, Capacity, advance, check_load, completing_unloads, has_work, make_plan, remaining,
    waiting_by_contract,
)

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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ships At Sea Logistics")
        self.resize(1150, 720)
        self.settings = QSettings("SasLogistics", "Planner")
        self.path = self.settings.value("snapshotPath", default_snapshot_path())
        self.snap = Snapshot()
        self.mtime = None
        self.sea_km: dict = {}  # (port, port) -> km by sea; empty until sea routes are known
        self.sea_routes: dict = {}  # "A|B" -> the route itself, for drawing on the map
        self.sea_source = None  # what the routes in use were worked out from: (depth grid mtime, port positions)
        self.sea_job = None  # a background route calculation in progress
        self.sea_note = "Distances are straight lines between ports."
        self.committed = None  # the plan being followed; it is kept until something calls for a new one
        self.committed_at = ""
        self.known_cargo: set[str] = set()  # cargo that existed when that plan was made

        self.status = QLabel()
        self.stale = QLabel()
        self.stale.setWordWrap(True)
        self.stale.setStyleSheet("color: #e5484d; font-weight: bold;")  # a red that reads on light and dark themes
        self.stale.hide()
        self.units = QComboBox()
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
        aboard_layout = QVBoxLayout()
        aboard_layout.addWidget(self.aboard_summary)
        aboard_layout.addWidget(self.aboard)
        aboard_tab = QWidget()
        aboard_tab.setLayout(aboard_layout)
        self.tabs.addTab(aboard_tab, "On board")
        self.tabs.addTab(self.matrix, "Cargo by port")
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

    def _route_tab(self) -> QWidget:
        self.start_port = QComboBox()
        self.priority = QComboBox()
        for key, label in PRIORITIES.items():
            self.priority.addItem(label, key)
        self.priority.setCurrentIndex(max(0, self.priority.findData(self.settings.value("priority", "full"))))
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

        form = QFormLayout()
        form.addRow("Start port", self.start_port)
        form.addRow("Prioritise", self.priority)
        form.addRow("Boat weight limit", self.max_weight)
        form.addRow("Boat item limit", self.max_items)
        form.addRow("Stops to plan", self.max_stops)
        form.addRow(self.use_hold)
        form.addRow(self.stacking)
        form.addRow(self.no_splits)
        form.addRow(self.highlight)
        self.plan_summary = QLabel()
        self.plan_summary.setWordWrap(True)
        replan_button = QPushButton("Replan from here")
        replan_button.setToolTip("Throw the current route away and plan a new one from the latest snapshot.")
        replan_button.clicked.connect(self.replan)
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
                               (self.highlight, "toggled")):
            getattr(widget, signal).connect(lambda *_: self.replan_timer.start())

        panel = QWidget()
        panel.setLayout(side)
        panel.setFixedWidth(330)
        layout = QHBoxLayout()
        layout.addWidget(panel)
        layout.addWidget(self.plan_tree, 1)
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
        """Tells the mod which cargo to highlight at the port the player is in: what to load, and, marked
        with a U, what still has to come off the boat."""
        wanted = []
        first = plan.stops[0] if plan.stops else None
        if self.highlight.isChecked() and first and first.port == self.snap.docked_port():
            wanted = [i.guid for i in first.load] + [i.guid for i, _ in first.stage]
            wanted += [f"{i.guid} U" for i in first.unload + first.drop]
            for _, stored in completing_unloads(self.snap, first):  # the stored part of a contract being finished
                wanted += [i.guid for i in stored]
        if self.highlight.isChecked():  # cargo to take out to finish a contract, wherever the boat is docked
            for mission in self.snap.completable_at(self.snap.docked_port()):
                wanted += [i.guid for i in mission.to_take_out]
        target = Path(self.path).with_name("highlight.txt")
        try:
            if target.parent.is_dir():
                target.write_text("".join(guid + "\n" for guid in wanted), encoding="ascii")
        except OSError:
            pass  # highlighting is a nicety; the plan on screen is still right

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

    def replan(self):
        for key, widget in (("maxWeight", self.max_weight), ("maxItems", self.max_items), ("maxStops", self.max_stops)):
            self.settings.setValue(key, widget.value())
        self.settings.setValue("priority", self.priority.currentData())
        self.settings.setValue("useHold", "true" if self.use_hold.isChecked() else "false")
        self.settings.setValue("stacking", "true" if self.stacking.isChecked() else "false")
        self.settings.setValue("noSplits", "true" if self.no_splits.isChecked() else "false")
        self.settings.setValue("highlight", "true" if self.highlight.isChecked() else "false")
        self.plan_tree.clear()
        snap = self.snap
        start = self.start_port.currentData() or snap.here()
        if not start:
            self.plan_summary.setText("Pick a start port: the snapshot has no player or port positions.")
            return
        hold = None
        if self.use_hold.isChecked() and snap.hold_size:
            hold = Hold(snap.hold_size, snap.hold_sizes, self.stacking.isChecked())
        capacity = Capacity(self.max_weight.value(), self.max_items.value(), hold,
                            allow_splits=not self.no_splits.isChecked())
        self.committed = make_plan(snap, start, capacity, self.priority.currentData(), self.max_stops.value())
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
            self.replan()
            return
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
                         "warehouse at the stop closest to its destination.")
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
        lines.append("Item counts in the Stop rows are what is on board when leaving.")
        self.plan_summary.setText("\n\n".join(lines))
