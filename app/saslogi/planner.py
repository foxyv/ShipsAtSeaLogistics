"""Pickup-and-delivery planner with lookahead.

A beam search over port sequences: every partial route is extended to each port where something can
be delivered or loaded, once per loading style, and only the best few routes are kept at each depth.
One of the styles loads nothing, so a route can leave cargo behind and sail empty to a better port.
It is still a heuristic, not an exact optimiser. Distances are straight lines between ports.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace

from .model import AT_SEA, BOAT, CargoItem, Snapshot
from .packing import Hold

PRIORITIES = {
    "money": "Most money per km",  # the window relabels this with the chosen distance unit
    "stops": "Fewest stops",
    "full": "Fullest loads",
}


@dataclass
class Capacity:
    max_weight: float = 0.0  # 0 = no limit
    max_items: int = 0  # 0 = no limit
    hold: Hold | None = None  # cell grid of the boat's hold; None = do not check space
    blocked: int = 0  # hold cells taken by things the planner does not move
    allow_splits: bool = True  # may a stop take only part of a contract's cargo waiting there

    def start(self, onboard=(), positions=None) -> "Load":
        """The boat carrying `onboard`. Cargo with a known place in the hold (`positions`) stays there."""
        load = Load(self)
        for item in onboard:
            load.add(item, force=True, position=(positions or {}).get(item.guid))
        return load


class Load:
    """What is on the boat while a plan is being built, and where each item sits in the hold.

    Cargo is never re-stowed: it keeps its cells from the moment it is loaded until it comes off.
    """

    def __init__(self, capacity: Capacity):
        self.capacity = capacity
        self.weight = 0.0
        self.count = 0
        self.occupied = capacity.blocked
        self.masks: dict[str, int] = {}  # item guid -> the hold cells it occupies

    def copy(self) -> "Load":
        other = Load(self.capacity)
        other.weight, other.count, other.occupied = self.weight, self.count, self.occupied
        other.masks = dict(self.masks)
        return other

    def add(self, item: CargoItem, force: bool = False, position: int | None = None) -> bool:
        cap, hold, occupied = self.capacity, self.capacity.hold, self.occupied
        if hold and position:
            self.masks[item.guid] = position
            self.occupied |= position
            self.weight += item.weight
            self.count += 1
            return True
        if not force:
            if cap.max_items and self.count + 1 > cap.max_items:
                return False
            if cap.max_weight and self.weight + item.weight > cap.max_weight:
                return False
        if hold:
            if not force and hold.sizes is not None and item.size is not None and item.size not in hold.sizes:
                return False
            if item.cells:
                placed = hold.place(occupied, item.kind, item.cells)
                if placed is None and not force:
                    return False
                if placed is not None:
                    self.masks[item.guid] = placed & ~occupied
                    occupied = placed
        self.occupied = occupied
        self.weight += item.weight
        self.count += 1
        return True

    def fill(self) -> float | None:
        """Fraction of the tightest limit in use, or None when no limit is set."""
        cap, parts = self.capacity, []
        if cap.max_weight:
            parts.append(self.weight / cap.max_weight)
        if cap.max_items:
            parts.append(self.count / cap.max_items)
        if cap.hold:
            parts.append(self.occupied.bit_count() / cap.hold.cells)
        return max(parts) if parts else None


@dataclass
class Stop:
    port: str
    leg_km: float | None = 0.0
    unload: list[CargoItem] = field(default_factory=list)  # delivered from the boat
    hand_in: list[CargoItem] = field(default_factory=list)  # already stored at their destination
    load: list[CargoItem] = field(default_factory=list)
    drop: list[CargoItem] = field(default_factory=list)  # left in storage here, the closest the route gets
    stage: list[tuple[CargoItem, str]] = field(default_factory=list)  # (cargo, port to leave it at): moved closer
    unload_all: bool = False  # nothing that arrived on the boat stays on it
    aboard: list[str] = field(default_factory=list)  # guids of the cargo meant to be on board when leaving
    onboard_count: int = 0
    onboard_weight: float = 0.0
    fill: float | None = None  # how full the boat is when leaving
    earned: float = 0.0


@dataclass
class Plan:
    stops: list[Stop] = field(default_factory=list)
    left_behind: int = 0  # undelivered items the plan did not reach within the stop limit

    @property
    def total_km(self) -> float:
        return sum(s.leg_km or 0.0 for s in self.stops)

    @property
    def total_money(self) -> float:
        return sum(s.earned for s in self.stops)

    @property
    def delivered(self) -> int:
        return sum(len(s.unload) + len(s.hand_in) for s in self.stops)

    @property
    def dropped(self) -> int:
        """Cargo that was on board at the start and is left in a warehouse instead of delivered."""
        return sum(len(s.drop) for s in self.stops) - self.staged

    @property
    def staged(self) -> int:
        """Cargo picked up only to be carried nearer to its destination."""
        return sum(len(s.stage) for s in self.stops)

    @property
    def has_unknown_distance(self) -> bool:
        return any(s.leg_km is None for s in self.stops)


def waiting_by_contract(snap: Snapshot, plan: Plan) -> list[dict[str, int]]:
    """For each stop, how many items of each contract (by mission id) are sitting at that port when the
    boat arrives. A load that takes fewer than this is a partial load of that contract."""
    taken: set[str] = set()
    counts = []
    for stop in plan.stops:
        here: dict[str, int] = defaultdict(int)
        for item in snap.undelivered():
            if item.location == stop.port and item.dest != stop.port and item.guid not in taken:
                here[item.mission.id] += 1
        counts.append(dict(here))
        taken |= {i.guid for i in stop.load} | {i.guid for i, _ in stop.stage}
    return counts


def advance(plan: Plan, docked: str | None) -> Plan | None:
    """The plan from the stop the boat is now at onward, for following a plan as it is carried out.

    At sea (`docked` is None) nothing changes. Docking at a stop further along drops the stops before
    it. Docking somewhere the plan does not go returns None: the plan no longer applies.
    """
    if docked is None:
        return plan
    ports = [stop.port for stop in plan.stops]
    if docked not in ports:
        return None
    return Plan(stops=plan.stops[ports.index(docked):], left_behind=plan.left_behind)


def remaining(plan: Plan, snap: Snapshot) -> Plan:
    """The plan with the work already done taken out, judged against the latest snapshot.

    At the first stop, cargo that has come off the boat is no longer listed to unload and cargo that
    is aboard is no longer listed to load. Anything delivered since the plan was made disappears.
    """
    fresh = {item.guid: item for item in snap.items}
    stops = []
    for index, stop in enumerate(plan.stops):
        def current(items, keep=lambda item: True):
            found = [fresh[i.guid] for i in items if i.guid in fresh]
            return [i for i in found if not i.delivered and keep(i)]

        aboard = (lambda item: item.location == BOAT) if index == 0 else (lambda item: True)
        ashore = (lambda item: item.location != BOAT) if index == 0 else (lambda item: True)
        staged = {i.guid: port for i, port in stop.stage}
        stops.append(replace(
            stop, unload=current(stop.unload, aboard), drop=current(stop.drop, aboard), hand_in=current(stop.hand_in),
            load=current(stop.load, ashore), stage=[(i, staged[i.guid]) for i in current([i for i, _ in stop.stage], ashore)]))
    return Plan(stops=stops, left_behind=plan.left_behind)


@dataclass
class LoadCheck:
    """How the boat compares, right now, with what the plan wants at the stop it is at."""

    planned: int  # cargo the plan loads at this stop
    to_load: list[CargoItem]  # of that, what is not on the boat yet
    to_unload: list[CargoItem]  # cargo that should come off here and is still aboard
    extra: list[CargoItem]  # aboard, but the plan does not have it leaving this stop on the boat

    @property
    def loaded(self) -> int:
        return self.planned - len(self.to_load)

    @property
    def ready(self) -> bool:
        return not (self.to_load or self.to_unload or self.extra)


def check_load(plan: Plan, snap: Snapshot) -> LoadCheck | None:
    """Checks the boat against the plan's first stop, using the plan as it was made, not what is left of it."""
    if not plan.stops:
        return None
    stop = plan.stops[0]
    fresh = {item.guid: item for item in snap.items}

    def current(items):
        return [fresh[i.guid] for i in items if i.guid in fresh and not fresh[i.guid].delivered]

    planned = current(stop.load) + current([i for i, _ in stop.stage])
    coming_off = current(stop.unload) + current(stop.drop)
    expected = set(stop.aboard) | {i.guid for i in coming_off}
    return LoadCheck(
        planned=len(planned),
        to_load=[i for i in planned if i.location != BOAT],
        to_unload=[i for i in coming_off if i.location == BOAT],
        extra=[i for i in snap.undelivered() if i.location == BOAT and i.guid not in expected] if stop.aboard else [],
    )


def completing_unloads(snap: Snapshot, stop: Stop) -> list[tuple]:
    """Contracts that this stop's unloading finishes off, with the cargo to take out of the warehouse.

    When part of a contract is already in its destination's warehouse and the boat brings the rest,
    unloading is not the end of it: the stored part has to come out of the warehouse too. Each entry
    is (mission, the items in the warehouse). Contracts already wholly at the port are not included;
    `Snapshot.completable_at` covers those.
    """
    arriving: dict[str, set[str]] = defaultdict(set)
    for item in stop.unload:
        if item.dest == stop.port:
            arriving[item.mission.id].add(item.guid)
    found = []
    for mission in snap.missions:
        coming = arriving.get(mission.id)
        if not coming or mission.status != "InProgress":
            continue
        if all(item.guid in coming or item.location == mission.end for item in mission.items):
            stored = [item for item in mission.items if item.location == mission.end and item.holder == "warehouse"]
            if stored:
                found.append((mission, stored))
    return found


def has_work(plan: Plan) -> bool:
    return any(s.unload or s.hand_in or s.drop or s.load or s.stage for s in plan.stops)


def _weight(items) -> float:
    return sum(i.weight for i in items)


def _bulk(items) -> float:
    """Hold space the items take, in cells; falls back to weight when footprints are unknown."""
    return sum(len(i.cells) if i.cells else i.weight for i in items)


# How much of the cargo waiting at a port a route takes:
#   all       everything that fits
#   focused   cargo for at most one destination not already on board
#   selective only the batches paying better per km than the average batch at this port
#   none      nothing, so the boat can deadhead to somewhere better
LOAD_STYLES = ("all", "focused", "selective", "none")


def _choose_load(snap, port, available, onboard, capacity, priority, typical_km, style="all", base=None):
    """Picks what to load at `port`. Cargo for a destination already on board always goes first.

    A contract's cargo is taken together where it all fits; only then is leftover space filled with
    part of one further contract. `base` is the already packed `onboard` cargo, passed in when several styles
    are tried for one stop.
    """
    load = base.copy() if base is not None else capacity.start(onboard)
    if style == "none":
        return [], load
    max_new_dests = 1 if style == "focused" else None
    groups: dict[str, list[CargoItem]] = defaultdict(list)
    for item in available:
        groups[item.dest].append(item)
    onboard_dests = {i.dest for i in onboard}
    if style == "selective" and groups:
        rate = {dest: sum(i.value for i in items) / (snap.distance_km(port, dest) or typical_km)
                for dest, items in groups.items()}
        average = sum(rate.values()) / len(rate)
        groups = {dest: items for dest, items in groups.items() if dest in onboard_dests or rate[dest] >= average}

    def key(dest):
        items = groups[dest]
        km = snap.distance_km(port, dest) or typical_km
        if priority == "money":
            rank = sum(i.value for i in items) / km
        elif priority == "stops":
            rank = len(items)
        else:
            rank = _bulk(items)  # big single-destination batches fill the boat and empty it in one stop
        return (dest not in onboard_dests, -rank, dest or "")

    chosen: list[CargoItem] = []
    opened: set[str] = set()  # destinations not on board before this stop that now have cargo loaded
    split: list[tuple[str, list[CargoItem]]] = []  # contracts that did not fit whole

    def allowed(dest):
        return dest in onboard_dests or dest in opened or max_new_dests is None or len(opened) < max_new_dests

    for dest in sorted(groups, key=key):
        if not allowed(dest):
            continue
        contracts: dict[str, list[CargoItem]] = defaultdict(list)
        for item in sorted(groups[dest], key=lambda i: (-i.value, i.guid)):
            contracts[item.mission.id].append(item)
        for batch in sorted(contracts.values(), key=lambda b: (-sum(i.value for i in b), b[0].guid)):
            trial = load.copy()
            if all(trial.add(item) for item in batch):
                load = trial
                chosen += batch
                if dest not in onboard_dests:
                    opened.add(dest)
            else:
                split.append((dest, batch))
    for dest, batch in split if capacity.allow_splits else ():  # at most one contract is split per stop
        if not allowed(dest):
            continue
        added = [item for item in batch if load.add(item)]
        if added:
            chosen += added
            break
    return chosen, load


@dataclass
class _Route:
    """A partial plan during the search."""

    cur: str
    onboard: tuple
    at_port: dict
    stops: tuple = ()
    km: float = 0.0
    money: float = 0.0
    delivered: int = 0
    fill_sum: float = 0.0
    positions: dict = field(default_factory=dict)  # where each item on board sits in the hold

    def key(self):
        return (self.cur, frozenset(i.guid for i in self.onboard),
                frozenset(i.guid for held in self.at_port.values() for i in held))


def _visits(snap, route, port, capacity, priority, typical_km):
    """The route extended by a stop at `port`, once per loading style that makes something happen there."""
    here = route.at_port.get(port, ())
    staying = [i for i in route.onboard if i.dest != port]
    waiting = [i for i in here if i.dest != port]
    base = capacity.start(staying, route.positions)
    seen = set()
    for style in LOAD_STYLES:
        chosen, load = _choose_load(snap, port, waiting, staying, capacity, priority, typical_km, style, base)
        picked = frozenset(i.guid for i in chosen)
        if picked in seen:
            continue  # this style loads exactly what an earlier one did
        seen.add(picked)
        nxt = _visit(snap, route, port, here, staying, chosen, load, typical_km)
        if nxt is not None:
            yield nxt


def _visit(snap, route, port, here, staying, chosen, load, typical_km):
    stop = Stop(port=port, leg_km=snap.distance_km(route.cur, port) if route.stops else 0.0)
    stop.unload = [i for i in route.onboard if i.dest == port]
    stop.hand_in = [i for i in here if i.dest == port]
    stop.load = chosen
    if route.stops and not (stop.unload or stop.hand_in or stop.load):
        return None
    loaded = {i.guid for i in stop.load}
    onboard = tuple(staying) + tuple(stop.load)
    at_port = dict(route.at_port)
    at_port[port] = tuple(i for i in here if i.guid not in loaded and i.dest != port)
    stop.onboard_count, stop.onboard_weight, stop.fill = len(onboard), _weight(onboard), load.fill()
    stop.earned = sum(i.value for i in stop.unload + stop.hand_in)
    leg = stop.leg_km if stop.leg_km is not None else typical_km
    return _Route(cur=port, onboard=onboard, at_port=at_port, stops=route.stops + (stop,),
                  km=route.km + leg, money=route.money + stop.earned,
                  delivered=route.delivered + len(stop.unload) + len(stop.hand_in),
                  fill_sum=route.fill_sum + (stop.fill if stop.fill is not None else 1.0),
                  positions=load.masks)


def _objective(route, priority, typical_km, carried_credit=0.5, km_price=None):
    """How good a partial route is.

    Cargo still on board earns part credit so that loading is never penalised. For "money", `km_price`
    is what a km of sailing is charged at; without one the route is scored as a plain money-per-km ratio.
    """
    if priority == "money":
        earned = route.money + carried_credit * sum(i.value for i in route.onboard)
        if km_price is not None:
            return earned - km_price * route.km
        return earned / (route.km + typical_km)
    progress = route.delivered + carried_credit * len(route.onboard) - 0.01 * route.km / typical_km
    if priority == "stops":
        return progress
    return progress * (0.25 + route.fill_sum / max(1, len(route.stops)))


def make_plan(snap: Snapshot, start: str, capacity: Capacity, priority: str = "full", max_stops: int = 12,
              beam_width: int = 32) -> Plan:
    items = [i for i in snap.undelivered() if i.location is not None and i.dest]
    at_port: dict[str, tuple] = defaultdict(tuple)
    for item in items:
        if item.location != BOAT:
            at_port[item.location] += (item,)
    onboard = tuple(i for i in items if i.location == BOAT)

    known = [d for a in snap.ports for b in snap.ports if a < b and (d := snap.distance_km(a, b))]
    typical_km = sum(known) / len(known) if known else 1.0

    # Cargo already on the boat stays where the game has it, and anything else in the hold is in the way.
    positions = {}
    if capacity.hold:
        positions = {i.guid: mask for i in onboard if (mask := capacity.hold.mask(i.placed))}
        capacity = replace(capacity, blocked=capacity.hold.mask(snap.hold_blocked))
    origin = _Route(start, onboard, dict(at_port), positions=positions)

    def search(width, km_price=None):
        """Beam search; returns the finished plans of the routes that survive to the stop limit."""
        def score(route):
            return _objective(route, priority, typical_km, km_price=km_price)

        # Routes are only compared with others of the same length.
        beam = list(_visits(snap, origin, start, capacity, priority, typical_km))
        for _ in range(max_stops - 1):
            grown: dict = {}
            for route in beam:
                targets = ({i.dest for i in route.onboard} | {p for p, held in route.at_port.items() if held}) - {route.cur}
                extended = False
                for port in sorted(targets):
                    for nxt in _visits(snap, route, port, capacity, priority, typical_km):
                        extended = True
                        key = nxt.key()
                        if key not in grown or score(nxt) > score(grown[key]):
                            grown[key] = nxt
                if not extended:
                    grown.setdefault(route.key(), route)  # finished: nothing left to do, keep it in the running
            if all(route in beam for route in grown.values()):
                break
            beam = sorted(grown.values(), key=score, reverse=True)[:beam_width if width is None else width]
        # The search scores routes before they are tidied up, and tidying can change them a lot: pickups
        # that never get delivered are dropped, and the stops made only for them go too. So every
        # surviving route is finished, and the plans are compared on what they really do.
        return [_finish(snap, route, onboard, capacity, items, positions) for route in beam]

    if priority != "money":
        plans = search(None)
        return max(plans, key=_plan_scorer(plans, priority, typical_km))

    # Money per km. A plain ratio is best served by delivering one rich load and then doing nothing,
    # which is not a plan. So a quick narrow search first finds what rate is achievable, and the real
    # search then charges every km at most of that rate: a route keeps going for as long as the extra
    # distance still earns at least that much.
    scout = search(SCOUT_WIDTH)
    km_price = KM_PRICE_SHARE * max(plan.total_money / (plan.total_km + typical_km) for plan in scout)
    plans = search(None, km_price) + scout
    return max(plans, key=lambda plan: plan.total_money - km_price * plan.total_km)


SCOUT_WIDTH = 6
KM_PRICE_SHARE = 0.7  # of the best money-per-km rate the scout search finds


def _plan_scorer(plans, priority, typical_km):
    """How finished plans are ranked for the "stops" and "full" priorities."""
    def progress(plan):
        return plan.delivered - 0.01 * plan.total_km / typical_km

    if priority == "stops":
        return progress
    return lambda plan: progress(plan) * (0.25 + sum(s.fill if s.fill is not None else 1.0 for s in plan.stops)
                                          / max(1, len(plan.stops)))


STAGE_GAIN = 0.75  # staging must bring cargo to within this fraction of its present distance


def _sail(stops, started_with, capacity, positions):
    """Walks the plan stop by stop. Returns what is on board leaving each stop, how it is stowed, and
    the loads that did not fit. Cargo keeps its place in the hold until it comes off."""
    onboard, legs, loads, misfits = list(started_with), [], [], []
    for stop in stops:
        gone = {i.guid for i in stop.drop}
        onboard = [i for i in onboard if i.dest != stop.port and i.guid not in gone]
        load = capacity.start(onboard, positions)
        for item in stop.load + [i for i, _ in stop.stage]:
            if not load.add(item):
                misfits.append(item)
                load.add(item, force=True)
            onboard = onboard + [item]
        positions = load.masks
        legs.append(onboard)
        loads.append(load)
    return legs, loads, misfits


def _finish(snap, route, started_with, capacity, items, positions) -> Plan:
    """Turns the winning route into a plan.

    The search gives part credit for cargo on board, so a route can pick up cargo for a port it does
    not reach before the stop limit. Those pickups are removed, stops left with nothing to do go too,
    and the space that leaves is then used to carry other cargo nearer to where it is going.
    """
    delivered = {i.guid for stop in route.stops for i in stop.unload}
    stops: list[Stop] = []
    for stop in route.stops:
        load = [i for i in stop.load if i.guid in delivered]
        if stops and not (stop.unload or stop.hand_in or load):
            continue
        if stops and stops[-1].port == stop.port:  # the stop between two visits was dropped
            stops[-1].load += load
            stops[-1].hand_in += stop.hand_in
            continue
        leg = snap.distance_km(stops[-1].port, stop.port) if stops else 0.0
        stops.append(replace(stop, load=load, unload=list(stop.unload), hand_in=list(stop.hand_in),
                             drop=[], stage=[], leg_km=leg))

    # Cargo that was already on board but is not delivered gets left at the stop nearest its destination.
    for item in started_with:
        if item.guid in delivered:
            continue
        reach = [(km, index) for index, stop in enumerate(stops)
                 if stop.port != AT_SEA and (km := snap.distance_km(stop.port, item.dest)) is not None]
        if reach:
            stops[min(reach)[1]].drop.append(item)

    # Spare space carries waiting cargo onward: to its destination if a later stop is that port,
    # otherwise to the later stop nearest its destination, as long as that is a real improvement.
    # Without the removed pickups the hold fills in a different order, and now and then something the
    # search had room for no longer goes in. Anything like that is taken out of the plan as well.
    while misfits := _sail(stops, started_with, capacity, positions)[2]:
        unwanted = {i.guid for i in misfits}
        if not capacity.allow_splits:  # the rest of that contract's pickup goes with it
            for stop in stops:
                broken = {i.mission.id for i in stop.load if i.guid in unwanted}
                unwanted |= {i.guid for i in stop.load if i.mission.id in broken}
        for stop in stops:
            stop.earned -= sum(i.value for i in stop.unload if i.guid in unwanted)
            stop.load = [i for i in stop.load if i.guid not in unwanted]
            stop.unload = [i for i in stop.unload if i.guid not in unwanted]
        delivered -= unwanted
    handled = delivered | {i.guid for stop in stops for i in stop.hand_in + stop.load}
    for k, stop in enumerate(stops):
        waiting = []
        for item in items:
            if item.location != stop.port or item.guid in handled or item.dest == stop.port:
                continue
            here = snap.distance_km(stop.port, item.dest)
            later = [(km, j) for j in range(k + 1, len(stops))
                     if (km := snap.distance_km(stops[j].port, item.dest)) is not None]
            if here is None or not later:
                continue
            km, j = min(later)
            if km <= here * STAGE_GAIN:
                waiting.append((km > 0, km - here, -item.value, item.guid, item, j))
        # A contract's waiting cargo goes together or not at all, so this never splits one up.
        batches: dict[tuple, list] = defaultdict(list)
        order = []
        for _, _, _, _, item, j in sorted(waiting):  # deliveries first, then the biggest gains
            if (item.mission.id, j) not in batches:
                order.append((item.mission.id, j))
            batches[(item.mission.id, j)].append(item)
        for mission_id, j in order:
            batch = batches[(mission_id, j)]
            delivers = stops[j].port == batch[0].dest
            pickup, setdown = (stop.load, stops[j].unload) if delivers else (stop.stage, stops[j].drop)
            pickup += batch if delivers else [(item, stops[j].port) for item in batch]
            setdown += batch
            if _sail(stops, started_with, capacity, positions)[2]:
                del pickup[-len(batch):]  # it does not all fit somewhere along the way
                del setdown[-len(batch):]
                continue
            if delivers:
                stops[j].earned += sum(item.value for item in batch)
            handled.update(item.guid for item in batch)

    legs, loads, _ = _sail(stops, started_with, capacity, positions)
    arriving = list(started_with)
    for stop, onboard, load in zip(stops, legs, loads):
        still = {i.guid for i in onboard}
        stop.unload_all = bool(stop.unload) and not any(i.guid in still for i in arriving)
        stop.onboard_count, stop.onboard_weight, stop.fill = len(onboard), _weight(onboard), load.fill()
        stop.aboard = [i.guid for i in onboard]
        arriving = onboard
    plan = Plan(stops=stops)
    plan.left_behind = len(items) - plan.delivered
    return plan
