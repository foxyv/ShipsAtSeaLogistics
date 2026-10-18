"""The files the planner shares with the mods.

  crane_orders.txt, highlight.txt   the planner writes them; the operator mod and the Logistics mod read them:
                                    one GUID a line, " U" after those to unload;
  loadfail.txt                      the operator mod writes it; the planner reads it: one GUID a line;
  snapshot.json                     the Logistics mod writes it; the planner reads it.

Each side is tested against the same sample file (tests/fixtures/*.golden.txt, kept identical in the
SasCraneOperator repository), so a change to the format on one side fails a test on that side. The last test
runs the two programs together, when the SasCraneOperator repository is next to this one.
"""

import importlib.util
import os
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from saslogi.audit import Audit  # noqa: E402
from saslogi.model import read_no_room  # noqa: E402
from saslogi.planner import Plan, Stop  # noqa: E402
from tests.lua_harness import load_mod  # noqa: E402
from tests.test_planner import mission, store  # noqa: E402
from tests.test_scoring import world  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
GUID_LINE = re.compile(r"^[0-9A-F]{32}( U)?$")
SIBLING = Path(__file__).resolve().parents[3] / "SasCraneOperator"


class Checkbox:
    def __init__(self, on):
        self.on = on

    def isChecked(self):
        return self.on


def g(digit):
    return digit * 32


def write_files(folder, highlight=True, orders=True):
    """What the planner writes for a stop at Port_A where the boat is docked: one piece to load, one to carry
    closer, one to unload, one only to be left in the warehouse, and a finished contract to take out."""
    ui = pytest.importorskip("saslogi.ui")
    snap = world([mission("mL", "B", [g("1")]), mission("mS", "C", [g("2")]), mission("mU", "A", [g("3")]),
                  mission("mD", "C", [g("4")]), mission("mT", "A", [g("5")])],
                 [store("warehouse", "A", [g("1"), g("2"), g("5")]), store("boatCargoArea", None, [g("3"), g("4")])])
    snap.docked_port = lambda: "Port_A"
    item = {i.guid: i for i in snap.items}
    stop = Stop(port="Port_A", load=[item[g("1")]], stage=[(item[g("2")], "Port_B")], unload=[item[g("3")]],
                drop=[item[g("4")]])
    me = SimpleNamespace(snap=snap, path=str(Path(folder) / "snapshot.json"), audit=Audit(),
                         highlight=Checkbox(highlight), crane_orders=Checkbox(orders))
    ui.MainWindow._write_highlights(me, Plan(stops=[stop]))
    return {name: (Path(folder) / name).read_text() for name in ("highlight.txt", "crane_orders.txt", "removable.txt")}


def test_the_planner_writes_what_to_load_what_to_unload_and_what_to_take_out():
    with tempfile.TemporaryDirectory() as folder:
        files = write_files(folder)
    # The piece the route puts off here (4) is ordered off with a U. The piece to take out to finish a contract (5) is
    # only highlighted: a plain line in crane_orders.txt means "load onto the boat", and the operator would
    # carry the piece the player puts on the dock for the delivery back aboard.
    orders = f"{g('1')}\n{g('2')}\n{g('3')} U\n{g('4')} U\n"
    assert files["crane_orders.txt"] == orders
    assert files["highlight.txt"] == orders + f"{g('5')}\n"
    assert all(GUID_LINE.match(line) for line in (orders + g("5")).splitlines())
    # and it is listed in removable.txt as well.
    assert files["removable.txt"] == f"{g('4')}\n"


def test_a_box_that_is_ticked_off_leaves_its_file_empty():
    with tempfile.TemporaryDirectory() as folder:
        files = write_files(folder, orders=False)
        assert files["crane_orders.txt"] == "" and files["removable.txt"] == ""
    with tempfile.TemporaryDirectory() as folder:
        assert write_files(folder, highlight=False)["highlight.txt"] == ""


def test_the_planner_reads_the_no_room_file_the_operator_writes():
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "loadfail.txt").write_text((FIXTURES / "loadfail.golden.txt").read_text())
        assert read_no_room(Path(folder) / "snapshot.json") == frozenset(
            {"11112222333344445555666677778888", "AAAABBBBCCCCDDDDEEEEFFFF00001111"})


def test_the_logistics_mod_reads_the_orders_file_as_the_planner_writes_it():
    mod = load_mod(files={"SasLogistics/highlight.txt": (FIXTURES / "marks.golden.txt").read_text()})
    wanted, count = mod.test.ReadHighlights()
    assert count == 4
    colour = {guid: "orange" if wanted[guid].G == mod.test.ORANGE.G else "green" for guid in wanted.keys()}
    assert colour == {"11112222333344445555666677778888": "green", "AAAABBBBCCCCDDDDEEEEFFFF00002222": "green",
                      "AAAABBBBCCCCDDDDEEEEFFFF00001111": "orange", "0123456789ABCDEF0123456789ABCDEF": "orange"}


def test_a_line_with_a_letter_the_readers_do_not_know_is_ignored_not_taken_for_a_load():
    mod = load_mod(files={"SasLogistics/highlight.txt": f"{g('1')} D\n{g('2')} U\n"})
    wanted, count = mod.test.ReadHighlights()
    assert count == 1 and list(wanted.keys()) == [g("2")]


@pytest.mark.skipif(not SIBLING.exists(), reason="the SasCraneOperator repository is not next to this one")
def test_the_two_programs_agree_when_run_together():
    spec = importlib.util.spec_from_file_location("operator_harness", SIBLING / "tests" / "lua_harness.py")
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    with tempfile.TemporaryDirectory() as folder:
        files = write_files(folder)
    # what the planner wrote is what the operator mod reads
    operator = harness.load_mod(files={"SasLogistics/crane_orders.txt": files["crane_orders.txt"],
                                       "SasLogistics/removable.txt": files["removable.txt"]})
    to_load, to_unload, count = operator.test.ReadMarks()
    assert set(to_load.keys()) == {g("1"), g("2")} and set(to_unload.keys()) == {g("3"), g("4")} and count == 4
    assert set(operator.g.ReadRemovable().keys()) == {g("4")}
    # and what the operator mod writes is what the planner reads
    operator.g.MarkNoRoom(
        operator.lua.eval("function(hold, boat) return { hold = hold, boat = boat } end")(
            harness.FakeArea(pieces=[]), harness.GameObject("BP_Cargo_Boat_2_C")),
        harness.FakeCargo(harness.FakeItem(guid=(0x11112222, 0x33334444, 0x55556666, 0x77778888))))
    written = next(operator.folder_path.rglob("loadfail.txt"))
    assert read_no_room(written.with_name("snapshot.json")) == frozenset({"11112222333344445555666677778888"})
