"""Loads the snapshot.json written by the SasLogistics mod into plain objects the planner and UI share."""

from __future__ import annotations

import json
import math
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

BOAT = "@boat"  # pseudo port id for cargo that is on a boat
AT_SEA = "@sea"  # pseudo port id for where the boat is while it is under way between ports

SPAWNED_TAG = "Mission.Data.Spawned"
BOARDED_TAG = "Mission.Rewards.Delivery.BoardedReward"
DELIVERED_TAG = "Mission.Rewards.Delivery.DeliverededReward"  # the game's own spelling
START_TAG = "Mission.Descriptor.Start"
END_TAG = "Mission.Descriptor.End"
ID_TAG = "Mission.Descriptor.MissionID"

CARGO_SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
CM_PER_KM = 100_000.0
DOCKED_KM = 0.3  # a boat this close to a port counts as being in it; neighbouring ports are 0.5 km apart
DOCK_AREA_KM = 0.12  # or this close to one of a port's dock cargo areas, wherever the port's own position is


@dataclass
class Port:
    id: str
    name: str
    location: tuple[float, float, float] | None = None


@dataclass
class CargoItem:
    guid: str
    mission: "Mission"
    name: str = "?"
    weight: float = 0.0
    size: int | None = None
    location: str | None = None  # port id, BOAT, or None when the item was not found anywhere
    holder: str = ""  # warehouse / cargoArea / boatCargoArea
    delivered: bool = False
    boarded: bool = False
    value: float = 0.0  # money still to be earned by loading and delivering this item
    kind: str = ""  # item definition name, e.g. DA_MediumCargo_Crate
    cells: list | None = None  # grid cells this cargo type occupies in a hold
    placed: list | None = None  # the hold cells it occupies right now, when it is on the boat being driven

    @property
    def dest(self) -> str | None:
        return self.mission.end

    @property
    def on_dock(self) -> bool:
        """Out on a port's dock (a cargo area), as opposed to inside its warehouse."""
        return self.holder == "cargoArea"


@dataclass
class Mission:
    id: str
    title: str
    status: str
    kind: str
    start: str | None
    end: str | None
    money_load: float = 0.0
    money_delivery: float = 0.0
    xp: float = 0.0
    items: list[CargoItem] = field(default_factory=list)
    deadline_ticks: int | None = None  # when the game's calendar ends the contract, in UE date time ticks

    @property
    def short_id(self) -> str:
        """The contract ID the game shows, e.g. #DL-E06448AD: Unreal's StrCrc32 of the mission GUID's digits."""
        if not re.fullmatch(r"[0-9A-F]{32}", self.id):
            return ""
        return f"#DL-{zlib.crc32(self.id.encode('utf-32-le')):08X}"

    @property
    def label(self) -> str:
        return f"{self.short_id} {self.title}".strip()

    @property
    def to_take_out(self) -> list[CargoItem]:
        """Cargo to take out of the destination warehouse to finish this contract.

        A contract stays open while its cargo sits in the warehouse at its destination, even though
        the game already counts each item as delivered. This is only non-empty once the whole
        contract is at the destination, since taking out part of it finishes nothing.
        """
        if self.status != "InProgress" or not self.items or not self.end:
            return []
        if any(item.location != self.end for item in self.items):
            return []
        return [item for item in self.items if item.holder == "warehouse"]

    @property
    def remaining_value(self) -> float:
        return sum(item.value for item in self.items)


@dataclass
class Boat:
    name: str
    owned: bool
    location: tuple[float, float, float] | None
    payload: float | None
    path: str
    max_speed_kn: float | None = None


@dataclass
class HoldItem:
    """One thing in the hold of the boat being driven; `cargo` is set when it belongs to a contract."""

    name: str
    weight: float
    cargo: CargoItem | None = None


@dataclass
class Snapshot:
    time: str = ""
    ports: dict[str, Port] = field(default_factory=dict)
    docks: list = field(default_factory=list)  # (port id, location) of every dock cargo area
    missions: list[Mission] = field(default_factory=list)
    items: list[CargoItem] = field(default_factory=list)
    boats: list[Boat] = field(default_factory=list)
    player_location: tuple[float, float, float] | None = None
    hold_size: tuple[int, int, int] | None = None  # cell grid of the owned boat's hold
    hold_sizes: list[int] | None = None  # cargo size classes that hold accepts
    cell_cm: float | None = None
    boat_name: str = ""  # the owned boat nearest the player, i.e. the one being driven
    aboard: list[HoldItem] = field(default_factory=list)
    hold_cells_used: int = 0
    hold_blocked: list = field(default_factory=list)  # hold cells taken by things the planner does not move: not contract cargo, or cargo already delivered
    delivered_aboard: list = field(default_factory=list)  # delivered contract cargo that is still in the hold
    unmarked: list = field(default_factory=list)  # cargo the game lists as delivered that is not at its destination
    sea_km: dict = field(default_factory=dict)  # (port, port) -> km by sea, when sea routes have been worked out
    errors: list[str] = field(default_factory=list)
    cruise_knots: float | None = None  # how fast the owned boat being driven sails
    now_ticks: int | None = None  # the game calendar's time now, in the same ticks as a contract's deadline
    game_speed: float = 1.0  # game seconds that pass in a real second

    def sail_hours(self, km: float) -> float:
        """Game hours to sail `km`: the real time it takes at the boat's cruising speed, times how many
        game seconds pass in a real second."""
        knots = self.cruise_knots or DEFAULT_KNOTS
        return km * 1000 / (knots * KNOT_MPS) / 3600 * self.game_speed

    def hours_left(self, mission: Mission) -> float | None:
        """Game hours until the contract's deadline, or None when it has none (or the clock is unknown)."""
        if mission.deadline_ticks is None or self.now_ticks is None:
            return None
        return (mission.deadline_ticks - self.now_ticks) / TICKS_PER_HOUR

    def port_name(self, port_id: str | None) -> str:
        if port_id == BOAT:
            return "On boat"
        if port_id == AT_SEA:
            return "In transit"
        if port_id is None:
            return "Not found"
        port = self.ports.get(port_id)
        return port.name if port else port_id.removeprefix("Port_")

    def place_name(self, item: CargoItem) -> str:
        """Where an item is, telling the dock apart from the warehouse: "Fenes" or "Fenes (On the Dock)"."""
        name = self.port_name(item.location)
        return f"{name} (On the Dock)" if item.on_dock else name

    def distance_km(self, a: str | None, b: str | None) -> float | None:
        """Distance by sea where a sea route is known, otherwise the straight line, which ignores land."""
        if a == b:
            return 0.0
        if AT_SEA in (a, b):
            # From the boat's own position there is no worked-out sea route, so this is a straight line.
            port, here = self.ports.get(b if a == AT_SEA else a or ""), self.boat_location()
            if not port or not port.location or not here:
                return None
            return math.dist(here[:2], port.location[:2]) / CM_PER_KM
        by_sea = self.sea_km.get((a, b))
        if by_sea is not None:
            return by_sea
        pa, pb = self.ports.get(a or ""), self.ports.get(b or "")
        if not pa or not pb or not pa.location or not pb.location:
            return None
        return math.dist(pa.location[:2], pb.location[:2]) / CM_PER_KM

    def nearest_port(self, location) -> str | None:
        located = [p for p in self.ports.values() if p.location]
        if not location or not located:
            return None
        return min(located, key=lambda p: math.dist(p.location[:2], location[:2])).id

    def boat_location(self):
        """Where the boat being driven is; the player's own position when no owned boat is known."""
        owned = [b for b in self.boats if b.owned and b.location]
        if self.player_location and owned:
            return min(owned, key=lambda b: math.dist(b.location[:2], self.player_location[:2])).location
        return self.player_location

    def current_port(self) -> str | None:
        """The nearest port, however far away it is."""
        return self.nearest_port(self.boat_location())

    def docked_port(self) -> str | None:
        """The port the boat is actually in, or None while it is out at sea.

        Beside one of a port's dock cargo areas counts first: a port's far docks (the large-cargo
        ones) can be further from the port's own position than a neighbouring port is.
        """
        here = self.boat_location()
        if here is None:
            return None
        near = [(math.dist(loc[:2], here[:2]), port) for port, loc in self.docks if loc and port in self.ports]
        if near:
            distance, port = min(near)
            if distance / CM_PER_KM <= DOCK_AREA_KM:
                return port
        port = self.current_port()
        if port is None:
            return None
        return port if math.dist(self.ports[port].location[:2], here[:2]) / CM_PER_KM <= DOCKED_KM else None

    def here(self) -> str | None:
        """Where a plan made now should start: the port the boat is in, or AT_SEA while it is under way.

        Falls back to the nearest port when the boat's position is not known at all.
        """
        docked = self.docked_port()
        if docked:
            return docked
        return AT_SEA if self.boat_location() else self.current_port()

    def completable_at(self, port: str | None) -> list[Mission]:
        """Contracts that can be finished at this port by taking their cargo out of its warehouse."""
        return [m for m in self.missions if m.end == port and m.to_take_out]

    def undelivered(self) -> list[CargoItem]:
        return [i for i in self.items if not i.delivered and i.mission.status == "InProgress"]


TICKS_PER_HOUR = 36_000_000_000  # a UE date time tick is 100 ns
KNOT_MPS = 0.514444
DEFAULT_KNOTS = 12.0  # when the boat's speed is not known
CRUISE_SHARE = 0.9  # of its top speed


def _ticks(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _vec(value) -> tuple[float, float, float] | None:
    return tuple(value) if isinstance(value, list) and len(value) == 3 else None


def _placed_cells(shape, placement) -> list:
    """Where the game has put an item: its footprint turned by the placement's yaw and moved to its position.

    The turn direction was checked against the game's own occupancy map for a loaded hold.
    """
    turn = math.radians(placement.get("yaw") or 0.0)
    cos, sin = round(math.cos(turn)), round(math.sin(turn))
    px, py, pz = placement["position"]
    return [(px + x * cos - y * sin, py + x * sin + y * cos, pz + z) for x, y, z in shape]


def parse_snapshot(data: dict) -> Snapshot:
    snap = Snapshot(time=data.get("time", ""), errors=list(data.get("errors") or []))
    calendar = data.get("calendar") or {}
    snap.now_ticks = _ticks(calendar.get("nowTicks"))
    snap.game_speed = float(calendar.get("gameSpeed") or 1.0)
    for raw in data.get("ports") or []:
        if raw.get("id"):
            snap.ports[raw["id"]] = Port(raw["id"], raw.get("name") or raw["id"], _vec(raw.get("location")))
    snap.player_location = _vec((data.get("player") or {}).get("location"))
    for raw in data.get("docks") or []:
        if raw.get("port") and raw.get("location"):
            snap.docks.append((raw["port"], _vec(raw["location"])))
    for raw in data.get("boats") or []:
        snap.boats.append(Boat(raw.get("boatName") or raw.get("class") or "Boat", bool(raw.get("owned")),
                               _vec(raw.get("location")), raw.get("payload"), raw.get("path", ""),
                               raw.get("maxSpeed")))

    snap.cell_cm = data.get("cellSize")
    footprints = data.get("footprints") or {}
    # A boat and its hold live in the same streamed level, so their object paths share a prefix.
    def level(path: str) -> str:
        return path.split(" ", 1)[-1].split(":")[0]  # drop the leading class name and the object part

    owned = [b for b in snap.boats if b.owned]
    owned_levels = {level(b.path) for b in owned}
    current = None
    if owned:
        placed = [b for b in owned if b.location] if snap.player_location else []
        current = min(placed, key=lambda b: math.dist(b.location[:2], snap.player_location[:2])) if placed else owned[0]
        snap.boat_name = current.name
        if current.max_speed_kn:
            snap.cruise_knots = CRUISE_SHARE * float(current.max_speed_kn)
    aboard_raw: list[dict] = []
    placed_cells: dict[str, list] = {}  # item guid -> the hold cells it occupies now
    where: dict[str, tuple[str | None, str, dict]] = {}
    for container in data.get("containers") or []:
        kind = container.get("kind", "")
        place = container.get("port")
        if kind == "boatCargoArea":
            mine = not owned_levels or level(container.get("path", "")) in owned_levels
            place = BOAT if mine else None
            if mine and (current is None or level(container.get("path", "")) == level(current.path)):
                aboard_raw += container.get("items") or []
                kinds = {raw.get("guid"): raw.get("def") for raw in container.get("items") or []}
                for placement in container.get("placements") or []:
                    shape = footprints.get(kinds.get(placement.get("guid")))
                    if shape:
                        placed_cells[placement["guid"]] = _placed_cells(shape, placement)
                snap.hold_cells_used += len(container.get("occupied") or [])
                if snap.hold_size is None and container.get("gridSize"):
                    snap.hold_size = tuple(container["gridSize"])
                    snap.hold_sizes = container.get("sizes") or None
        for raw in container.get("items") or []:
            if raw.get("guid"):
                where[raw["guid"]] = (place, kind, raw)

    for raw in data.get("missions") or []:
        context = raw.get("context") or {}
        spawned = context.get(SPAWNED_TAG) or []
        if not spawned:
            continue  # not a cargo mission
        mission = Mission(id=context.get(ID_TAG) or raw.get("path", ""), title=raw.get("title") or "?",
                          status=raw.get("status") or "?", kind=raw.get("asset") or raw.get("class") or "",
                          start=context.get(START_TAG), end=context.get(END_TAG),
                          deadline_ticks=_ticks(raw.get("deadlineTicks")))
        for reward in raw.get("rewards") or []:
            amount = reward.get("amount") or 0
            if "Money" in (reward.get("type") or ""):
                if "deliver" in (reward.get("stage") or "").lower():
                    mission.money_delivery += amount
                else:
                    mission.money_load += amount
            elif "XP" in (reward.get("type") or ""):
                mission.xp += amount
        boarded = set(context.get(BOARDED_TAG) or [])
        delivered = set(context.get(DELIVERED_TAG) or [])
        for guid in spawned:
            place, kind, item_raw = where.get(guid, (None, "", {}))
            # The game keeps a piece marked delivered once it has reached its destination, even if it is
            # then taken out again (out of the warehouse, say) and carried off. A piece found anywhere
            # but its destination is not delivered: it is planned as cargo still to deliver. A piece
            # that is nowhere (consumed on delivery) stays delivered.
            flagged = guid in delivered
            away = (flagged and mission.status == "InProgress" and place is not None
                    and mission.end is not None and place != mission.end)
            item = CargoItem(guid=guid, mission=mission, name=item_raw.get("name") or "?",
                             weight=item_raw.get("weight") or 0.0, size=item_raw.get("size"),
                             location=place, holder=kind, delivered=flagged and not away, boarded=guid in boarded,
                             kind=item_raw.get("def") or "", cells=footprints.get(item_raw.get("def")) or None)
            if away:
                snap.unmarked.append(item)
            if not item.delivered:
                item.value = mission.money_delivery / len(spawned)
                if not item.boarded:
                    item.value += mission.money_load / len(spawned)
            mission.items.append(item)
            snap.items.append(item)
        snap.missions.append(mission)

    by_guid = {item.guid: item for item in snap.items}
    open_guids = {item.guid for item in snap.undelivered()}
    for guid, cells in placed_cells.items():
        if guid in by_guid:
            by_guid[guid].placed = cells
            if guid not in open_guids:
                # Delivered (or the contract is over) but still standing in the hold: it takes room all
                # the same, and the plan does not move it.
                snap.hold_blocked += cells
                snap.delivered_aboard.append(by_guid[guid])
        else:
            snap.hold_blocked += cells
    snap.aboard = [HoldItem(raw.get("name") or "?", raw.get("weight") or 0.0, by_guid.get(raw.get("guid")))
                   for raw in aboard_raw]
    return snap


def read_no_room(path: str | Path) -> frozenset[str]:
    """The cargo the crane operator reports cannot be fitted into the hold: one guid a line in
    loadfail.txt next to the snapshot. Nothing there, or no file, means nothing is reported."""
    try:
        text = Path(path).with_name("loadfail.txt").read_text(encoding="ascii", errors="ignore")
    except OSError:
        return frozenset()
    return frozenset(line.strip().upper() for line in text.splitlines() if line.strip())


def load_snapshot(path: str | Path) -> Snapshot:
    return parse_snapshot(json.loads(Path(path).read_text(encoding="utf-8")))
