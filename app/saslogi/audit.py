"""An audit log of what the planner decided, so that "what happened?" can be answered afterwards.

One JSON object per line (JSON Lines) in `audit.jsonl` in the planner's folder under %APPDATA%: a line for every
route the planner works out (what made it, the settings, every stop with its loads, unloads, stages and drops,
each piece by name and id) and for every change in the orders sent to the crane operator and the highlights in
the game. The file is only ever appended to. When it passes 200 MB it rolls over: it becomes `audit.1.jsonl`
(older ones move up to `.2` and `.3`, and the oldest is deleted) and a new `audit.jsonl` starts, so the log
never takes more than about 800 MB.
Writing never raises: a log that cannot be written must not stop the planner.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

LIMIT_BYTES = 200_000_000  # a file is set aside when it passes this
KEEP = 3                   # and this many older files are kept


class Audit:
    def __init__(self, path: str | os.PathLike | None = None, limit: int = LIMIT_BYTES, keep: int = KEEP):
        self.path = Path(path) if path else None
        self.limit = limit
        self.keep = keep
        self._last: dict[str, str] = {}  # kind -> the last thing written under it, for record_if_changed

    def record(self, kind: str, **fields) -> None:
        if not self.path:
            return
        line = json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **fields}, ensure_ascii=False,
                          default=str)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_file() and self.path.stat().st_size > self.limit:
                self._roll_over()
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def _numbered(self, number: int) -> Path:
        return self.path.with_name(f"{self.path.stem}.{number}{self.path.suffix}")

    def _roll_over(self) -> None:
        """audit.jsonl becomes audit.1.jsonl, audit.1 becomes audit.2, and so on; what would pass `keep` is deleted."""
        self._numbered(self.keep).unlink(missing_ok=True)
        for number in range(self.keep - 1, 0, -1):
            older = self._numbered(number)
            if older.is_file():
                os.replace(older, self._numbered(number + 1))
        os.replace(self.path, self._numbered(1))

    def record_if_changed(self, kind: str, **fields) -> bool:
        """Writes the line only when what it says differs from the last line of this kind. Returns whether it did."""
        said = json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)
        if self._last.get(kind) == said:
            return False
        self._last[kind] = said
        self.record(kind, **fields)
        return True


def items_of(items) -> list[dict]:
    """Pieces of cargo as they go in the log: the id, the name, where it is going, and the contract."""
    return [{"guid": i.guid, "name": i.name, "to": i.dest, "contract": i.mission.id} for i in items]


def plan_of(plan, snap) -> dict:
    """A plan as it goes in the log: every stop, with every piece."""
    stops = []
    for stop in plan.stops:
        stops.append({
            "port": stop.port, "name": snap.port_name(stop.port), "leg_km": stop.leg_km,
            "unload": items_of(stop.unload), "hand_in": items_of(stop.hand_in), "load": items_of(stop.load),
            "drop": items_of(stop.drop), "stage": [{**items_of([i])[0], "leave_at": port} for i, port in stop.stage],
            "onboard": stop.onboard_count, "fill": stop.fill, "earned": stop.earned,
        })
    return {"stops": stops, "left_behind": plan.left_behind}


def counts_of(plan) -> list[str]:
    """One short line per stop, for reading the log without the pieces."""
    return [f"{s.port}: unload {len(s.unload)}, hand in {len(s.hand_in)}, load {len(s.load)}, "
            f"drop {len(s.drop)}, stage {len(s.stage)}" for s in plan.stops]
