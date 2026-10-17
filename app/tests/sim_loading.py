"""Simulates loading a boat's hold, to compare the planner's promise with how the crane operator places.

The planner (saslogi.planner) accepts a load when each piece, taken in turn, has a first-fit place in
the hold as it already stands. The crane operator then places the same pieces in whatever order they
come off the dock, by a policy of its own. A load the planner accepted can fail if that policy wastes
space the planner's did not. This measures how often, for several placing policies and orders, with
the real cargo footprints from the game (tests/footprints_sample.json).

Run:  python tests/sim_loading.py [trials] [seed]
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from saslogi.packing import rotations  # noqa: E402

FOOT = json.loads(Path(__file__).with_name("footprints_sample.json").read_text())
# How often each kind turns up in a manifest (the mediums seen on the Dr. Phil's runs).
WEIGHTS = {
    "DA_MediumCargo_Box": 3, "DA_MediumCargo_Cardboxes": 3, "DA_MediumCargo_Crate": 3, "DA_MediumCargo_Planks": 3,
    "DA_MediumCargo_DiredFish": 1, "DA_MediumCargo_Comm": 2, "DA_MediumCargo_Engine": 1.5, "DA_MediumCargo_WoodChips": 1.5,
    "DA_MediumCargo_Barrel": 1.5, "DA_MediumCargo_PipeStack": 3, "DA_MediumCargo_Pipe": 1,
}
SIZE = (12, 12, 4)
CRANE = (6.0, -2.0)  # where the cranes stand against the hold, for the operator's "furthest first"


class Hold:
    def __init__(self, size=SIZE):
        self.sx, self.sy, self.sz = size
        self.cells = self.sx * self.sy * self.sz
        self._places: dict[str, list] = {}

    def bit(self, x, y, z):
        return 1 << (x + self.sx * (y + self.sy * z))

    def places(self, kind):
        """Every in-bounds placement as (mask, support mask, dz, x0, y0, x1, y1), in the planner's order."""
        if kind not in self._places:
            found = []
            for shape in rotations(FOOT[kind]):
                wx, wy, wz = (max(c[a] for c in shape) + 1 for a in range(3))
                unsupported = [c for c in shape if (c[0], c[1], c[2] - 1) not in shape]
                for dz in range(self.sz - wz + 1):
                    for dy in range(self.sy - wy + 1):
                        for dx in range(self.sx - wx + 1):
                            mask = support = 0
                            for x, y, z in shape:
                                mask |= self.bit(x + dx, y + dy, z + dz)
                            if dz:
                                for x, y, z in unsupported:
                                    support |= self.bit(x + dx, y + dy, z + dz - 1)
                            found.append((mask, support, dz, dx, dy, dx + wx - 1, dy + wy - 1))
            self._places[kind] = found
        return self._places[kind]


HOLD = Hold()


def valid(occ, p):
    return not occ & p[0] and occ & p[1] == p[1]


# ---- placing policies: (occupied, kind, pieces) -> a placement or None -------------------------

def first_fit(occ, kind, pieces):
    """The planner's own: the first place that takes it, rotations first, then low to high."""
    for p in HOLD.places(kind):
        if valid(occ, p):
            return p
    return None


def _floor_blocked(occ, x, y):
    return x < 0 or y < 0 or x >= HOLD.sx or y >= HOLD.sy or bool(occ & HOLD.bit(x, y, 0))


def neat_then_first_fit(occ, kind, pieces):
    """The crane operator as it is: squarely on a same-size piece, else the floor spot with the most of
    its edge against a wall or another piece (furthest from the crane among the equally neat), else the
    first place the game finds."""
    best, best_key = None, None
    for p in HOLD.places(kind):
        if not valid(occ, p):
            continue
        mask, support, dz, x0, y0, x1, y1 = p
        if dz == 0:
            wx, wy = x1 - x0 + 1, y1 - y0 + 1
            touching = sum(_floor_blocked(occ, x0 + i, y0 - 1) + _floor_blocked(occ, x0 + i, y1 + 1) for i in range(wx))
            touching += sum(_floor_blocked(occ, x0 - 1, y0 + j) + _floor_blocked(occ, x1 + 1, y0 + j) for j in range(wy))
            neat = touching / (2 * (wx + wy))
        else:
            under = [q for q in pieces if q[0] == kind and (q[1], q[2], q[3], q[4]) == (x0, y0, x1, y1) and q[5] + 1 == dz]
            if not under:
                continue
            neat = 1.1
        cx, cy = (x0 + x1 + 1) / 2, (y0 + y1 + 1) / 2
        dist = ((cx - CRANE[0]) ** 2 + (cy - CRANE[1]) ** 2) ** 0.5
        key = (int(neat * 20), dist)
        if best_key is None or key > best_key:
            best, best_key = p, key
    return best or first_fit(occ, kind, pieces)


def _contact(occ, p):
    """Faces of the placement touching a wall, the floor or another piece."""
    mask = p[0]
    n, m = 0, mask
    cells = []
    while m:
        low = m & -m
        i = low.bit_length() - 1
        cells.append((i % HOLD.sx, (i // HOLD.sx) % HOLD.sy, i // (HOLD.sx * HOLD.sy)))
        m ^= low
    for x, y, z in cells:
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            nx, ny, nz = x + dx, y + dy, z + dz
            if not (0 <= nx < HOLD.sx and 0 <= ny < HOLD.sy and 0 <= nz < HOLD.sz):
                n += 1
            elif occ & HOLD.bit(nx, ny, nz):
                n += 1
    return n


def max_contact(occ, kind, pieces):
    """The valid place with the most of the piece against walls, the floor and other pieces."""
    best, best_key = None, None
    for p in HOLD.places(kind):
        if valid(occ, p):
            key = (_contact(occ, p), -p[2])
            if best_key is None or key > best_key:
                best, best_key = p, key
    return best


POLICIES = {
    "planner first-fit": first_fit,
    "operator now (neat, then first-fit)": neat_then_first_fit,
    "max contact": max_contact,
}


# ---- the trials ------------------------------------------------------------------------------

def volume(kind):
    return len(FOOT[kind])


def bbox_height(kind):
    return max(c[2] for c in FOOT[kind]) + 1


def _box(kind):
    return tuple(max(c[a] for c in FOOT[kind]) - min(c[a] for c in FOOT[kind]) + 1 for a in range(3))


def _long_first(kind):
    x, y, z = _box(kind)
    return (-max(x, y), -x * y * z)


def _footprint_first(kind):
    x, y, z = _box(kind)
    return (-x * y, -z)


def _box_volume_first(kind):
    x, y, z = _box(kind)
    return (-x * y * z, -max(x, y))


# Orders the operator might take the pieces in; each is a sort key, smallest first.
ORDERS = {"longest side, then volume": _long_first, "floor area, then height": _footprint_first,
          "box volume, then longest": _box_volume_first}


def place_all(policy, occ, pieces, kinds):
    """Places the kinds in order; returns (occupied, ok, the kinds that did not go in)."""
    missed = []
    for kind in kinds:
        p = policy(occ, kind, pieces)
        if p is None:
            missed.append(kind)
            continue
        occ |= p[0]
        pieces.append((kind, p[3], p[4], p[5], p[6], p[2] + bbox_height(kind) - 1, p[2]))
    return occ, not missed, missed


_LUA = None


def tidy_can_make_room(pieces, kind):
    """Would the operator's whole-hold rearranging (CompactSpace in the mod, run through lupa) find a
    way to open a space for this kind? A bounding-box test, as the mod makes it."""
    global _LUA
    from lupa import LuaRuntime
    if _LUA is None:
        src = (Path(__file__).resolve().parents[3] / "SasCraneOperator" / "SasCraneOperator" / "Scripts" / "main.lua").read_text(encoding="utf-8")
        a = src.index("COMPACT_MOVES = 12")
        b = src.index("-- Plans the clearing of a space in the hold for a piece that does not fit.")
        _LUA = LuaRuntime(unpack_returned_tuples=True)
        _LUA.execute(src[a:b])
    boxes = _LUA.table()
    for i, (k, x0, y0, x1, y1, z1, z0) in enumerate(pieces, 1):
        boxes[i] = _LUA.table_from({"id": i, "low": _LUA.table_from({"X": x0, "Y": y0, "Z": z0}),
                                    "size": _LUA.table_from({"X": x1 - x0 + 1, "Y": y1 - y0 + 1, "Z": z1 - z0 + 1}),
                                    "anchor": _LUA.table_from({"X": 0, "Y": 0, "Z": 0})})
    xs = [c[0] for c in FOOT[kind]]; ys = [c[1] for c in FOOT[kind]]; zs = [c[2] for c in FOOT[kind]]
    want = _LUA.table_from({"X": max(xs) - min(xs) + 1, "Y": max(ys) - min(ys) + 1, "Z": max(zs) - min(zs) + 1})
    lim = _LUA.table_from({"X": SIZE[0], "Y": SIZE[1], "Z": SIZE[2]})
    return _LUA.globals().CompactSpace(boxes, lim, want) is not None


def trial(rng, policy, order, cap=1.0, tidy=False):
    kinds = list(WEIGHTS)
    weights = [WEIGHTS[k] for k in kinds]
    # What is aboard already, placed by the policy under test (the operator loaded it earlier).
    occ, pieces = 0, []
    target = rng.uniform(0.25, 0.55) * HOLD.cells
    while occ.bit_count() < target:
        occ, _, _ = place_all(policy, occ, pieces, [rng.choices(kinds, weights)[0]])
    # The planner accepts what fits, in its order, by its own first-fit on the layout as it stands.
    manifest = [rng.choices(kinds, weights)[0] for _ in range(14)]
    planned, probe = [], occ
    for kind in manifest:
        p = first_fit(probe, kind, [])
        if p is not None and (probe | p[0]).bit_count() <= cap * HOLD.cells:
            planned.append(kind)
            probe |= p[0]
    if order == "shuffled":
        rng.shuffle(planned)
    elif order == "biggest first":
        planned.sort(key=volume, reverse=True)
    elif order in ORDERS:
        planned.sort(key=ORDERS[order])
    _, ok, missed = place_all(policy, occ, pieces, planned)
    if not ok and tidy:
        ok = all(tidy_can_make_room(pieces, kind) for kind in missed)
    return ok, len(planned), len(missed), (probe.bit_count() / HOLD.cells)


def margin_sweep(trials=300, seed=1):
    print(f"{trials} trials per row: the planner stops accepting pieces once the hold would be over this share full")
    print(f"{'policy':40} {'order':14} {'cap':>5} {'fail':>6} {'planned fill':>13}")
    for name in ("operator now (neat, then first-fit)",):
        for order in ("shuffled", "biggest first"):
            for cap in (1.0, 0.95, 0.9, 0.85, 0.8, 0.75):
                rng = random.Random(seed)
                fails, fill = 0, 0.0
                for _ in range(trials):
                    ok, count, missed, f = trial(rng, POLICIES[name], order, cap)
                    fails += not ok
                    fill += f
                print(f"{name:40} {order:14} {100 * cap:4.0f}% {100 * fails / trials:5.1f}% {100 * fill / trials:12.0f}%")


def order_sweep(trials=400, seed=1):
    print(f"{trials} trials per row, operator as it is now, no margin")
    print(f"{'order':32} {'fail':>6} {'fail with tidy':>15}")
    for order in ("shuffled", "biggest first", *ORDERS):
        res = []
        for tidy in (False, True):
            rng = random.Random(seed)
            res.append(sum(not trial(rng, POLICIES["operator now (neat, then first-fit)"], order, 1.0, tidy)[0] for _ in range(trials)))
        print(f"{order:32} {100 * res[0] / trials:5.1f}% {100 * res[1] / trials:14.1f}%")


def tidy_sweep(trials=300, seed=1):
    print(f"{trials} trials per row: failures that the whole-hold rearranging would rescue (bounding boxes)")
    print(f"{'order':14} {'cap':>5} {'fail':>6} {'fail with tidy':>15}")
    for order in ("shuffled", "biggest first"):
        for cap in (1.0, 0.9, 0.85):
            res = []
            for tidy in (False, True):
                rng = random.Random(seed)
                res.append(sum(not trial(rng, POLICIES["operator now (neat, then first-fit)"], order, cap, tidy)[0] for _ in range(trials)))
            print(f"{order:14} {100 * cap:4.0f}% {100 * res[0] / trials:5.1f}% {100 * res[1] / trials:14.1f}%")


def main(trials=300, seed=1):
    print(f"{trials} trials per row, hold {SIZE[0]}x{SIZE[1]}x{SIZE[2]}, planner accepts what first-fits; "
          "'fail' = a piece it accepted could not be placed")
    print(f"{'policy':40} {'order':14} {'fail':>6} {'pieces lost':>12} {'planned fill':>13}")
    for name, policy in POLICIES.items():
        for order in ("planner order", "shuffled", "biggest first"):
            rng = random.Random(seed)
            fails = lost = planned = 0
            fill = 0.0
            for _ in range(trials):
                ok, count, missed, f = trial(rng, policy, order)
                fails += not ok
                lost += missed
                planned += count
                fill += f
            print(f"{name:40} {order:14} {100 * fails / trials:5.1f}% {lost:5d}/{planned:<6d} {100 * fill / trials:12.0f}%")


if __name__ == "__main__":
    if "orders" in sys.argv:
        order_sweep()
    elif "tidy" in sys.argv:
        tidy_sweep(200, 1)
    elif "margin" in sys.argv:
        margin_sweep(300, 1)
    else:
        main(int(sys.argv[1]) if len(sys.argv) > 1 else 300, int(sys.argv[2]) if len(sys.argv) > 2 else 1)
