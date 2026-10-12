"""First-fit packing of cargo footprints into a boat hold's cell grid.

The hold and each cargo type are sets of grid cells exported from the game. Occupancy is one big
integer bitmask so testing a placement is a couple of integer operations.
"""

from __future__ import annotations

Cell = tuple[int, int, int]


def _normalise(cells) -> frozenset[Cell]:
    mx, my, mz = (min(c[axis] for c in cells) for axis in range(3))
    return frozenset((x - mx, y - my, z - mz) for x, y, z in cells)


def rotations(cells) -> list[frozenset[Cell]]:
    """The distinct shapes from turning the footprint in 90 degree steps about the vertical axis."""
    shape, found = [tuple(c) for c in cells], []
    for _ in range(4):
        normal = _normalise(shape)
        if normal not in found:
            found.append(normal)
        shape = [(-y, x, z) for x, y, z in shape]
    return found


class Hold:
    def __init__(self, size, sizes=None, stacking: bool = True):
        self.size = tuple(size)
        self.sizes = set(sizes) if sizes else None  # cargo size classes the hold accepts; None = any
        self.stacking = stacking
        self.cells = self.size[0] * self.size[1] * self.size[2]
        self._cache: dict[str, list[tuple[int, int]]] = {}
        self._placed: dict[tuple[int, str], int | None] = {}  # the search packs the same loads over and over

    def _bit(self, x, y, z) -> int:
        return 1 << (x + self.size[0] * (y + self.size[1] * z))

    def mask(self, cells) -> int:
        """Occupancy bits for cells given in hold coordinates; anything outside the hold is ignored."""
        bits = 0
        for x, y, z in cells or ():
            if 0 <= x < self.size[0] and 0 <= y < self.size[1] and 0 <= z < self.size[2]:
                bits |= self._bit(x, y, z)
        return bits

    def placements(self, key: str, cells) -> list[tuple[int, int]]:
        """Every in-bounds position of the footprint as (cells mask, cells that must be filled beneath)."""
        if key not in self._cache:
            sx, sy, sz = self.size
            found = []
            for shape in rotations(cells):
                wx, wy, wz = (max(c[axis] for c in shape) + 1 for axis in range(3))
                unsupported = [c for c in shape if (c[0], c[1], c[2] - 1) not in shape]
                for dz in range(sz - wz + 1 if self.stacking else min(1, sz - wz + 1)):
                    for dy in range(sy - wy + 1):
                        for dx in range(sx - wx + 1):
                            mask = support = 0
                            for x, y, z in shape:
                                mask |= self._bit(x + dx, y + dy, z + dz)
                            if dz:
                                for x, y, z in unsupported:
                                    support |= self._bit(x + dx, y + dy, z + dz - 1)
                            found.append((mask, support))
            self._cache[key] = found
        return self._cache[key]

    def place(self, occupied: int, key: str, cells) -> int | None:
        """Returns the new occupancy with the footprint placed lowest-first, or None if it does not fit."""
        memo = (occupied, key)
        if memo not in self._placed:
            self._placed[memo] = None
            for mask, support in self.placements(key, cells):
                if not occupied & mask and occupied & support == support:
                    self._placed[memo] = occupied | mask
                    break
        return self._placed[memo]
