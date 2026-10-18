"""The audit log: one JSON line per decision, rolling over when it gets big, never raising."""

import json

from saslogi.audit import LIMIT_BYTES, Audit, counts_of, items_of, plan_of
from saslogi.planner import Capacity, make_plan

from tests.test_planner import mission, store
from tests.test_scoring import world


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_each_record_is_a_line_with_the_time_and_kind(tmp_path):
    audit = Audit(tmp_path / "audit.jsonl")
    audit.record("plan", cause="by hand", stops=2)
    audit.record("orders", port="Port_A")
    rows = lines(tmp_path / "audit.jsonl")
    assert [r["kind"] for r in rows] == ["plan", "orders"]
    assert rows[0]["cause"] == "by hand" and rows[0]["at"][:2] == "20"


def test_the_log_rolls_over_at_two_hundred_megabytes():
    assert LIMIT_BYTES == 200_000_000


def test_a_big_log_is_set_aside_and_a_new_one_started(tmp_path):
    audit = Audit(tmp_path / "audit.jsonl", limit=200)
    for number in range(30):
        audit.record("x", number=number, filler="y" * 20)
    assert (tmp_path / "audit.1.jsonl").is_file()
    assert (tmp_path / "audit.jsonl").stat().st_size <= 400  # the live file never grows far past the limit
    every = [r["number"] for name in ("audit.3.jsonl", "audit.2.jsonl", "audit.1.jsonl", "audit.jsonl")
             if (tmp_path / name).is_file() for r in lines(tmp_path / name)]
    assert every == sorted(every) and every[-1] == 29  # oldest first across the files, nothing out of order


def test_only_a_few_old_files_are_kept(tmp_path):
    audit = Audit(tmp_path / "audit.jsonl", limit=100, keep=2)
    for number in range(80):
        audit.record("x", number=number, filler="y" * 30)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["audit.1.jsonl", "audit.2.jsonl", "audit.jsonl"]  # no .3: the oldest was deleted


def test_a_record_that_cannot_be_written_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    Audit(blocker / "audit.jsonl").record("plan")  # the parent is a file: nothing can be written, nothing raised
    Audit(None).record("plan")


def test_record_if_changed_writes_only_when_the_content_changes(tmp_path):
    audit = Audit(tmp_path / "audit.jsonl")
    assert audit.record_if_changed("orders", highlight=["a"]) is True
    assert audit.record_if_changed("orders", highlight=["a"]) is False
    assert audit.record_if_changed("orders", highlight=["a", "b"]) is True
    assert len(lines(tmp_path / "audit.jsonl")) == 2


def test_a_plan_is_logged_with_every_stop_and_piece():
    snap = world([mission("m1", "B", ["g1"]), mission("m2", "C", ["g2"])], [store("warehouse", "A", ["g1", "g2"])])
    plan = make_plan(snap, "Port_A", Capacity())
    logged = plan_of(plan, snap)
    assert logged["stops"][0]["port"] == "Port_A"
    loaded = {i["guid"] for stop in logged["stops"] for i in stop["load"]}
    unloaded = {i["guid"] for stop in logged["stops"] for i in stop["unload"]}
    assert loaded == {"g1", "g2"} and unloaded == {"g1", "g2"}
    assert items_of(plan.stops[0].load)[0]["contract"] in ("m1", "m2")
    assert len(counts_of(plan)) == len(plan.stops) and counts_of(plan)[0].startswith("Port_A: unload 0")
    json.dumps(logged)  # all of it can be written
