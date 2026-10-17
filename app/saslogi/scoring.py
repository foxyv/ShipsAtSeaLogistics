"""The scoring model: how much a plan is worth, as a weighted sum of a few terms.

Every term is put on a 0..1 scale against what the whole open job would be worth, so that a weight of 1
means about the same importance for each. The terms:

    contracts   whole contracts delivered on time (a contract delivered in pieces counts in fractions)
    money       what the plan earns, less what sailing costs (a fixed price per km), as a share of all the
                money still to earn
    fill        how full the hold is on the legs sailed, weighted by their length (volume only)
    empty       the share of the distance sailed with nothing aboard   (a penalty)
    stranded    the share of the open cargo the plan leaves behind     (a penalty)
    late        the share of contracts with a deadline that arrive after it (a penalty); this is the
                top priority, so its weight is much the largest

A contract that arrives after its deadline is lost: it earns nothing and counts as late.
"""

from __future__ import annotations

from dataclasses import dataclass

STOP_HOURS = 0.5  # game hours spent at each port: docking, unloading and loading


@dataclass(frozen=True)
class Weights:
    contracts: float = 1.0
    money: float = 1.0
    fill: float = 0.5
    empty: float = 0.5
    stranded: float = 1.0
    late: float = 10.0

    def as_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in ("contracts", "money", "fill", "empty", "stranded", "late")}


# Starting points the planner window offers; the three old priorities have their own code paths.
PRESETS = {
    "balanced": Weights(),
    "money": Weights(contracts=0.5, money=3.0, fill=0.3, empty=0.5, stranded=0.5, late=10.0),
    "finish contracts": Weights(contracts=3.0, money=0.5, fill=0.3, empty=0.5, stranded=2.0, late=10.0),
    "full holds": Weights(contracts=0.5, money=0.5, fill=3.0, empty=1.0, stranded=0.5, late=10.0),
}


@dataclass(frozen=True)
class Scale:
    """What the whole open job is worth, for putting each term on a 0..1 scale."""

    contracts: float = 1.0
    value: float = 1.0
    items: float = 1.0
    urgent: float = 1.0  # contracts with a deadline still ahead


def scale_of(snap) -> Scale:
    open_items = [i for i in snap.undelivered() if i.dest]
    missions = {i.mission.id: i.mission for i in open_items}
    urgent = [m for m in missions.values() if (left := snap.hours_left(m)) is not None and left > 0]
    return Scale(contracts=max(1.0, float(len(missions))), value=max(1.0, sum(i.value for i in open_items)),
                 items=max(1.0, float(len(open_items))), urgent=max(1.0, float(len(urgent))))


def late_share(snap, item, arrival_hours: float) -> float:
    """The part of a contract this item stands for, if it arrives after the contract's deadline, else 0."""
    left = snap.hours_left(item.mission)
    if left is None or arrival_hours <= left:
        return 0.0
    return 1.0 / max(1, len(item.mission.items))


def contract_share(item) -> float:
    return 1.0 / max(1, len(item.mission.items))


def arrival_hours(snap, km_so_far: float, stops_so_far: int) -> float:
    """Game hours from now to arriving at a stop that is `km_so_far` along the route, after `stops_so_far`."""
    return snap.sail_hours(km_so_far) + STOP_HOURS * stops_so_far


def combine(weights: Weights, terms: dict[str, float]) -> float:
    return (weights.contracts * terms["contracts"] + weights.money * terms["money"] + weights.fill * terms["fill"]
            - weights.empty * terms["empty"] - weights.stranded * terms["stranded"] - weights.late * terms["late"])


def plan_terms(snap, plan, scale: Scale, km_price: float) -> dict[str, float]:
    """The scoring terms of a finished plan."""
    km, on_time, late, empty_km = 0.0, 0.0, 0.0, 0.0
    fill_km = 0.0
    for index, stop in enumerate(plan.stops):
        leg = stop.leg_km or 0.0
        km += leg
        if index:
            before = plan.stops[index - 1]
            fill_km += (before.fill if before.fill is not None else 1.0) * leg  # how full the leg was
            if before.onboard_count == 0:
                empty_km += leg
        arrival = arrival_hours(snap, km, index)
        for item in stop.unload + stop.hand_in:
            lost = late_share(snap, item, arrival)
            late += lost
            on_time += contract_share(item) - lost
    return {
        "contracts": on_time / scale.contracts,
        "money": (plan.total_money - km_price * km) / scale.value,
        "fill": fill_km / km if km else 0.0,
        "empty": empty_km / km if km else 0.0,
        "stranded": (plan.left_behind + plan.dropped) / scale.items,
        "late": late / scale.urgent,
    }
