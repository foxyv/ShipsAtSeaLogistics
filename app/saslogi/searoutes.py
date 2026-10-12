"""Sea routes between ports, worked out on the water-depth grid the mod samples from the game.

The grid says how deep the water is every `cell_cm` across the map. A cell is navigable when it has
at least the depth the game says boats need. Routes are shortest paths over navigable cells, then
straightened, so they follow the water around land instead of cutting across it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

NAVIGABLE_M = 5  # the game's own minimum water depth for boats
PORT_REACH_CELLS = 40  # how far from a port to look for navigable water
CACHE_VERSION = 3
_ORTHOGONAL, _DIAGONAL = 10, 14  # integer step costs, so the search can use buckets instead of a heap
_FAR = 1 << 30


@dataclass
class DepthGrid:
    origin_x: float
    origin_y: float
    cell_cm: float
    cols: int
    rows: int
    depth_m: list[int]  # row by row, west to east; negative is land
    header: str = ""

    def index(self, x: float, y: float) -> tuple[int, int]:
        return round((x - self.origin_x) / self.cell_cm), round((y - self.origin_y) / self.cell_cm)

    def position(self, index: int) -> tuple[float, float]:
        return (self.origin_x + (index % self.cols) * self.cell_cm, self.origin_y + (index // self.cols) * self.cell_cm)

    def navigable(self) -> bytearray:
        return bytearray(1 if depth >= NAVIGABLE_M else 0 for depth in self.depth_m)


def load_depth_grid(path: str | Path) -> DepthGrid | None:
    """Reads depthgrid.txt. Returns None if it is missing, still being written, or not what is expected."""
    try:
        with open(path, encoding="ascii") as file:
            header = file.readline().strip()
            fields = header.split()
            if len(fields) < 7 or fields[0] != "sasdepth":
                return None
            origin_x, origin_y, cell = float(fields[2]), float(fields[3]), float(fields[4])
            cols, rows = int(fields[5]), int(fields[6])
            depth = [int(value) for value in file.read().split()]
    except (OSError, ValueError):
        return None
    if len(depth) != cols * rows:
        return None
    return DepthGrid(origin_x, origin_y, cell, cols, rows, depth, header)


def nearest_navigable(grid: DepthGrid, nav: bytearray, x: float, y: float) -> int | None:
    """The navigable cell closest to a point, searched in growing rings; ports sit in shallow water."""
    col, row = grid.index(x, y)
    best, best_distance = None, None
    for ring in range(PORT_REACH_CELLS + 1):
        if best is not None and ring > best_distance:
            break
        for c in range(col - ring, col + ring + 1):
            for r in (row - ring, row + ring) if ring else (row,):
                best, best_distance = _closer(grid, nav, c, r, col, row, best, best_distance)
        for r in range(row - ring + 1, row + ring):
            for c in (col - ring, col + ring):
                best, best_distance = _closer(grid, nav, c, r, col, row, best, best_distance)
    return best


def _closer(grid, nav, c, r, col, row, best, best_distance):
    if 0 <= c < grid.cols and 0 <= r < grid.rows and nav[r * grid.cols + c]:
        distance = math.hypot(c - col, r - row)
        if best_distance is None or distance < best_distance:
            return r * grid.cols + c, distance
    return best, best_distance


def _search(nav: bytearray, cols: int, rows: int, source: int, targets: set[int]) -> list[int]:
    """Shortest paths from `source` over navigable cells; returns each reached cell's predecessor.

    Stops once every target is reached. Diagonal steps are not allowed to cut a land corner.
    """
    size = cols * rows
    cost = [_FAR] * size
    previous = [-1] * size
    cost[source] = 0
    buckets: list[list[int]] = [[source]]
    waiting = set(targets)
    last_col = cols - 1
    level = 0
    while level < len(buckets) and waiting:
        for cell in buckets[level]:
            if cost[cell] != level:
                continue  # reached more cheaply already
            if cell in waiting:
                waiting.discard(cell)
                if not waiting:
                    break
            col = cell % cols
            north, south = cell - cols, cell + cols
            has_north, has_south = north >= 0, south < size
            has_west, has_east = col > 0, col < last_col
            open_north = has_north and nav[north]
            open_south = has_south and nav[south]
            open_west = has_west and nav[cell - 1]
            open_east = has_east and nav[cell + 1]
            steps = []
            if open_north:
                steps.append((north, _ORTHOGONAL))
            if open_south:
                steps.append((south, _ORTHOGONAL))
            if open_west:
                steps.append((cell - 1, _ORTHOGONAL))
            if open_east:
                steps.append((cell + 1, _ORTHOGONAL))
            if open_north and open_west and nav[north - 1]:
                steps.append((north - 1, _DIAGONAL))
            if open_north and open_east and nav[north + 1]:
                steps.append((north + 1, _DIAGONAL))
            if open_south and open_west and nav[south - 1]:
                steps.append((south - 1, _DIAGONAL))
            if open_south and open_east and nav[south + 1]:
                steps.append((south + 1, _DIAGONAL))
            for neighbour, step in steps:
                reached = level + step
                if reached < cost[neighbour]:
                    cost[neighbour] = reached
                    previous[neighbour] = cell
                    while len(buckets) <= reached:
                        buckets.append([])
                    buckets[reached].append(neighbour)
        level += 1
    return previous


def _clear(nav: bytearray, cols: int, a: int, b: int) -> bool:
    """Whether the straight line between two cells stays over navigable water."""
    ac, ar, bc, br = a % cols, a // cols, b % cols, b // cols
    steps = 2 * max(abs(bc - ac), abs(br - ar))
    for step in range(1, steps):
        t = step / steps
        if not nav[round(ar + (br - ar) * t) * cols + round(ac + (bc - ac) * t)]:
            return False
    return True


def _straighten(nav: bytearray, cols: int, path: list[int], stride: int = 3) -> list[int]:
    """Pulls a stair-stepped grid path taut: keeps only the corners it needs to stay over water."""
    if len(path) <= 2:
        return path
    candidates = path[::stride]
    if candidates[-1] != path[-1]:
        candidates.append(path[-1])
    taut = [candidates[0]]
    for index in range(1, len(candidates) - 1):
        if not _clear(nav, cols, taut[-1], candidates[index + 1]):
            taut.append(candidates[index])
    taut.append(candidates[-1])
    # The corners so far all sit on the grid path, which only runs in eight directions, so a route
    # across open water still has a bend where the path changed direction. Adding points along each
    # segment and pulling taut again lets the bend be cut from part-way along, twice over.
    taut = _prune(nav, cols, taut)
    for _ in range(2):
        taut = _prune(nav, cols, _densify(taut, cols))
    return taut


def _prune(nav: bytearray, cols: int, points: list[int]) -> list[int]:
    """Drops every corner whose neighbours can see each other, until none can be dropped."""
    points = list(points)
    dropped = True
    while dropped and len(points) > 2:
        dropped = False
        for index in range(len(points) - 2, 0, -1):
            if _clear(nav, cols, points[index - 1], points[index + 1]):
                del points[index]
                dropped = True
    return points


def _densify(points: list[int], cols: int, spacing: int = 6) -> list[int]:
    """The same polyline with extra points about every `spacing` cells along each segment."""
    dense = [points[0]]
    for a, b in zip(points, points[1:]):
        ac, ar, bc, br = a % cols, a // cols, b % cols, b // cols
        pieces = max(1, max(abs(bc - ac), abs(br - ar)) // spacing)
        for piece in range(1, pieces):
            t = piece / pieces
            dense.append(round(ar + (br - ar) * t) * cols + round(ac + (bc - ac) * t))
        dense.append(b)
    return dense


def compute_routes(grid: DepthGrid, ports: dict[str, tuple[float, float]], progress=None) -> dict[str, dict]:
    """A route for every pair of ports that can be joined by water, keyed "A|B" with A < B.

    Each route has its length in km and its corner points in world coordinates (for drawing later).
    """
    nav = grid.navigable()
    anchors = {}
    for port, (x, y) in ports.items():
        cell = nearest_navigable(grid, nav, x, y)
        if cell is not None:
            anchors[port] = cell
    names = sorted(anchors)
    routes: dict[str, dict] = {}
    for done, name in enumerate(names):
        if progress:
            progress(done, len(names))
        others = names[done + 1:]
        if not others:
            break
        previous = _search(nav, grid.cols, grid.rows, anchors[name], {anchors[o] for o in others})
        for other in others:
            target = anchors[other]
            if target != anchors[name] and previous[target] < 0:
                continue  # no water route between them
            path = [target]
            while path[-1] != anchors[name]:
                path.append(previous[path[-1]])
            path.reverse()
            points = [ports[name]] + [grid.position(cell) for cell in _straighten(nav, grid.cols, path)] + [ports[other]]
            length = sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
            routes[f"{name}|{other}"] = {"km": length / 100_000.0, "points": [[round(x), round(y)] for x, y in points]}
    if progress:
        progress(len(names), len(names))
    return routes


def signature(grid: DepthGrid, ports: dict[str, tuple[float, float]]) -> str:
    """Identifies what a set of routes was worked out from, so a cache is only reused when it still applies."""
    where = ";".join(f"{name}:{round(x)},{round(y)}" for name, (x, y) in sorted(ports.items()))
    return f"v{CACHE_VERSION}|{NAVIGABLE_M}|{grid.header}|{where}"


def load_cache(path: str | Path, wanted: str) -> dict[str, dict] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("routes") if data.get("signature") == wanted else None


def save_cache(path: str | Path, wanted: str, routes: dict[str, dict]) -> None:
    try:
        Path(path).write_text(json.dumps({"signature": wanted, "routes": routes}), encoding="utf-8")
    except OSError:
        pass  # the routes are still usable for this session


def distances(routes: dict[str, dict]) -> dict[tuple[str, str], float]:
    """Route lengths in km, looked up by a pair of ports in either order."""
    table = {}
    for key, route in routes.items():
        a, b = key.split("|")
        table[(a, b)] = table[(b, a)] = route["km"]
    return table
