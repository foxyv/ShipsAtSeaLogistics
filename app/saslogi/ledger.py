"""The company's money: the game's ledger of transactions, kept by the planner beyond what the game remembers.

The mod writes the game's ledger (a date, an amount and a description each, the game keeping only its last
few) and the capital into every snapshot. The planner merges each snapshot's list into a ledger of its own,
saved in a file, so the history grows past the game's limit. Amounts are whole numbers of the game's smallest
unit (`raw`); the unit is worked out from the capital, which the mod gives both ways, so every amount is shown
exactly, with no rounding through a float.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path

FORMAT = 2   # 1 stored the entries newest first (a mistake); 2 stores them oldest first
UNITS = (1, 10, 100, 1000, 10_000, 100_000)
DEFAULT_UNIT = 100


@dataclass(frozen=True, order=True)
class Entry:
    year: int
    month: int      # 1 to 12
    day: int
    raw: int        # in the game's smallest unit; negative is money paid out
    description: str

    @property
    def date(self) -> str:
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"

    def to_json(self) -> list:
        return [self.year, self.month, self.day, str(self.raw), self.description]

    @staticmethod
    def from_json(row) -> "Entry":
        return Entry(int(row[0]), int(row[1]), int(row[2]), int(row[3]), str(row[4]))


def unit_from(capital_raw: int | None, capital_value: float | None) -> int:
    """How many raw units make one whole unit of money: the capital's raw number over the game's own
    conversion of it, rounded to a power of ten. When that cannot be told, hundredths."""
    if capital_raw and capital_value:
        ratio = abs(capital_raw / capital_value)
        best = min(UNITS, key=lambda unit: abs(unit - ratio))
        if abs(best - ratio) <= best * 0.01:
            return best
    return DEFAULT_UNIT


def exact(raw: int, unit: int, group: bool = False) -> str:
    """An amount written out exactly: 1250050 in hundredths is 12500.50. With `group`, thousands are spaced."""
    sign = "-" if raw < 0 else ""
    whole, part = divmod(abs(raw), unit)
    digits = len(str(unit)) - 1
    text = f"{whole:,}".replace(",", " ") if group else str(whole)
    return f"{sign}{text}" + (f".{part:0{digits}d}" if digits else "")


def kind(entry: Entry) -> str:
    """What sort of transaction: the game pays a finished contract as 'Job Payment'; any other line is money
    spent (negative) or earned (selling something, say)."""
    if entry.description.strip().lower() == "job payment":
        return "Contract payment"
    return "Spent" if entry.raw < 0 else "Earned"


def parse_entries(finance: dict) -> list[Entry]:
    """The entries of a snapshot's finance section, oldest first. The game lists them newest first (seen: the
    first is 31 August and the last 10 June), so the list is turned round unless its dates run the other way."""
    out = []
    for row in finance.get("transactions") or []:
        try:
            out.append(Entry(int(row["year"]), int(row["month"]) + 1, int(row["day"]), int(row["raw"]),
                             str(row.get("description") or "")))
        except (KeyError, TypeError, ValueError):
            continue  # a row the mod could not read all of is left out, not guessed
    if out and out[0].date <= out[-1].date and out[0].date != out[-1].date:
        return out  # dates rise: already oldest first
    return list(reversed(out))


class Ledger:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else None
        self.entries: list[Entry] = []
        self.unit = DEFAULT_UNIT
        self.capital_raw: int | None = None
        self.history_size: int | None = None
        self.note = ""
        self._load()

    # ------------------------------------------------------------------------------------------ keeping

    def _load(self):
        if not self.path or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries = [Entry.from_json(row) for row in data.get("entries", [])]
            dates = [e.date for e in entries]
            if data.get("format") != FORMAT or dates != sorted(dates):
                # A file from before the order was right, or one whose dates do not rise: it is not trusted. It is
                # set aside, and the game's own list fills a new one.
                self.note = ("The saved history was in the wrong order (an earlier version of the planner wrote it), so "
                             "it was set aside as ledger.old and the game's recent transactions were read again.")
                try:
                    os.replace(self.path, self.path.with_suffix(".old"))
                except OSError:
                    pass
                return
            self.entries = entries
            self.unit = int(data.get("unit") or DEFAULT_UNIT)
            self.capital_raw = int(data["capital_raw"]) if data.get("capital_raw") is not None else None
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            self.entries = []  # a damaged file is started again; the game's own list fills it back in

    def save(self):
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"format": FORMAT, "unit": self.unit, "capital_raw": self.capital_raw,
                                       "entries": [e.to_json() for e in self.entries]}), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # the ledger on screen is still right; it is saved again at the next look

    def clear(self):
        self.entries, self.note = [], ""
        self.save()

    # ------------------------------------------------------------------------------------------ merging

    def merge(self, finance: dict | None) -> int:
        """Adds what the game's list has that this ledger has not. Returns how many entries were added.

        The game's list is its most recent transactions; the ones this ledger has are older or the same. Where
        the end of this ledger matches the start of the game's list, the rest of the game's list is new. If the
        game's list lies within this ledger there is nothing to add. If the two share nothing, the game's list
        may be another save, or a gap of more than the game remembers: the old ledger is kept in a file beside
        it (.old) and a new one started, with a note saying so."""
        if not finance:
            return 0
        window = parse_entries(finance)
        try:
            self.capital_raw = int(finance["capitalRaw"]) if finance.get("capitalRaw") is not None else self.capital_raw
        except (TypeError, ValueError):
            pass
        value = finance.get("capital")
        self.unit = unit_from(self.capital_raw, value if isinstance(value, (int, float)) else None) \
            if value else self.unit
        self.history_size = finance.get("historySize") if isinstance(finance.get("historySize"), int) else self.history_size
        if not window:
            return 0
        if not self.entries:
            self.entries = list(window)
            self.save()
            return len(window)
        if _contains(self.entries, window):
            return 0
        for start in range(len(self.entries)):  # the longest overlap: the earliest start that matches
            tail = self.entries[start:]
            if window[:len(tail)] == tail:
                added = window[len(tail):]
                merged = self.entries + added
                dates = [e.date for e in merged]
                if dates == sorted(dates):
                    self.entries = merged
                    self.save()
                    return len(added)
                break  # the dates would not rise: not a real overlap, so start again below
        # no overlap
        self.note = ("The game's list of transactions and the saved history have nothing in common (another save, or "
                     "more transactions than the game remembers since the last look): the old history was set aside "
                     "and this one started again.")
        if self.path and self.path.is_file():
            try:
                os.replace(self.path, self.path.with_suffix(".old"))
            except OSError:
                pass
        self.entries = list(window)
        self.save()
        return len(window)

    # ------------------------------------------------------------------------------------------ reading

    def balances(self) -> list[int | None]:
        """The balance after each entry, counted back from the capital the game reports now. None when the
        capital is not known."""
        if self.capital_raw is None:
            return [None] * len(self.entries)
        out, balance = [None] * len(self.entries), self.capital_raw
        for index in range(len(self.entries) - 1, -1, -1):
            out[index] = balance
            balance -= self.entries[index].raw
        return out

    def rows(self) -> list[tuple[Entry, int | None]]:
        """Newest first, each with its balance after it."""
        return list(reversed(list(zip(self.entries, self.balances()))))

    def totals(self, entries: list[Entry]) -> tuple[int, int, int]:
        income = sum(e.raw for e in entries if e.raw > 0)
        spent = sum(e.raw for e in entries if e.raw < 0)
        return income, spent, income + spent


def _contains(whole: list, part: list) -> bool:
    size = len(part)
    return any(whole[i:i + size] == part for i in range(len(whole) - size + 1))


def write_csv(path: str | os.PathLike, rows: list[tuple[Entry, int | None]], unit: int) -> int:
    """Writes rows in the order given (oldest first is what a spreadsheet wants) with exact amounts. UTF-8 with a byte-order
    mark so that Excel reads the characters right. Returns the number of rows written."""
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Date", "Description", "Type", "Amount", "Balance after"])
        for entry, balance in rows:
            writer.writerow([entry.date, entry.description, kind(entry), exact(entry.raw, unit),
                             "" if balance is None else exact(balance, unit)])
    return len(rows)
