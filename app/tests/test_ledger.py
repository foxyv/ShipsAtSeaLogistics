"""The ledger: the game's list of transactions merged into a history the planner keeps, exact amounts, CSV."""

import csv

import pytest

from saslogi.ledger import Entry, Ledger, exact, kind, parse_entries, unit_from, write_csv


def tx(day, raw, description="x", month=9, year=1820):
    """A row as the mod writes it (month 0 to 11)."""
    return {"day": day, "month": month, "year": year, "raw": str(raw), "description": description}


def finance(rows, capital_raw=1000000, capital=10000.0, history=100):
    return {"transactions": rows, "capitalRaw": str(capital_raw), "capital": capital, "historySize": history}


def test_rows_are_read_with_the_month_counted_from_one():
    entries = parse_entries(finance([tx(3, 1250050, "Mission completed: Fish to Fenes")]))
    assert entries == [Entry(1820, 10, 3, 1250050, "Mission completed: Fish to Fenes")]
    assert entries[0].date == "1820-10-03"


def test_a_row_the_mod_could_not_read_all_of_is_left_out():
    assert parse_entries(finance([tx(3, 5), {"day": 4, "month": 9}, tx(5, 6)])) == [
        Entry(1820, 10, 3, 5, "x"), Entry(1820, 10, 5, 6, "x")]


@pytest.mark.parametrize("raw,unit,text", [
    (1250050, 100, "12500.50"), (-30000, 100, "-300.00"), (5, 100, "0.05"), (-5, 100, "-0.05"),
    (1234, 1, "1234"), (123456, 1000, "123.456"),
])
def test_amounts_are_written_out_exactly(raw, unit, text):
    assert exact(raw, unit) == text


def test_amounts_can_be_grouped_for_the_screen():
    assert exact(123456789, 100, group=True) == "1 234 567.89"


@pytest.mark.parametrize("raw,value,unit", [(987600, 9876.0, 100), (50, 50.0, 1), (12345000, 12345.0, 1000),
                                            (1000, 0.0, 100), (None, None, 100), (777, 5.3, 100)])
def test_the_unit_is_worked_out_from_the_capital(raw, value, unit):
    assert unit_from(raw, value) == unit


def test_the_first_look_takes_everything_and_it_is_kept_in_a_file(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger(path)
    assert ledger.merge(finance([tx(1, 100), tx(2, -50)])) == 2
    again = Ledger(path)  # a new run of the planner
    assert [e.raw for e in again.entries] == [100, -50]


def test_a_longer_game_list_adds_only_what_is_new():
    ledger = Ledger()
    ledger.merge(finance([tx(1, 1), tx(2, 2), tx(3, 3)]))
    assert ledger.merge(finance([tx(2, 2), tx(3, 3), tx(4, 4), tx(5, 5)])) == 2
    assert [e.raw for e in ledger.entries] == [1, 2, 3, 4, 5]  # the first one is past the game's memory, and kept


def test_the_same_list_again_adds_nothing():
    ledger = Ledger()
    ledger.merge(finance([tx(1, 1), tx(2, 2)]))
    assert ledger.merge(finance([tx(1, 1), tx(2, 2)])) == 0 and len(ledger.entries) == 2
    assert ledger.merge(finance([tx(2, 2)])) == 0  # a window within what is kept


def test_two_equal_transactions_are_both_kept():
    ledger = Ledger()
    ledger.merge(finance([tx(1, 10, "Port fee"), tx(1, 10, "Port fee")]))
    ledger.merge(finance([tx(1, 10, "Port fee"), tx(1, 10, "Port fee"), tx(2, 10, "Port fee")]))
    assert [e.raw for e in ledger.entries] == [10, 10, 10]


def test_a_list_with_nothing_in_common_starts_again_and_keeps_the_old_one_aside(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger(path)
    ledger.merge(finance([tx(1, 1), tx(2, 2)]))
    ledger.merge(finance([tx(9, 90, year=1830), tx(10, 91, year=1830)]))
    assert [e.raw for e in ledger.entries] == [90, 91]
    assert "nothing in common" in ledger.note
    assert (tmp_path / "ledger.old").is_file()


def test_the_balance_after_each_entry_is_the_capital_less_the_later_entries():
    ledger = Ledger()
    ledger.merge(finance([tx(1, 1000), tx(2, -250), tx(3, 500)], capital_raw=5000))
    # after the last entry the balance is the capital; before it, 500 less; before that, 250 more again
    assert ledger.balances() == [4750, 4500, 5000]


def test_without_the_capital_there_are_no_balances():
    ledger = Ledger()
    ledger.merge({"transactions": [tx(1, 1000)]})
    assert ledger.balances() == [None]


def test_totals_split_money_in_from_money_out():
    ledger = Ledger()
    ledger.merge(finance([tx(1, 1000), tx(2, -250), tx(3, 500)]))
    assert ledger.totals(ledger.entries) == (1500, -250, 1250)


def test_the_csv_has_exact_amounts_oldest_first_and_opens_in_excel(tmp_path):
    ledger = Ledger()
    ledger.merge(finance([tx(3, 1250050, "Mission completed, Fish to Fenes"), tx(4, -30000, 'Port "fee"')], capital_raw=1000000))
    out = tmp_path / "t.csv"
    count = write_csv(out, list(reversed(ledger.rows())), ledger.unit)
    assert count == 2
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")  # the byte-order mark
    rows = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0] == ["Date", "Description", "Type", "Amount", "Balance after"]
    assert rows[1] == ["1820-10-03", "Mission completed, Fish to Fenes", "Earned", "12500.50", "10300.00"]
    assert rows[2] == ["1820-10-04", 'Port "fee"', "Spent", "-300.00", "10000.00"]


def test_a_damaged_file_is_started_again(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("not json at all")
    ledger = Ledger(path)
    assert ledger.entries == [] and ledger.merge(finance([tx(1, 1)])) == 1


def test_clearing_empties_the_file_too(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger(path)
    ledger.merge(finance([tx(1, 1)]))
    ledger.clear()
    assert Ledger(path).entries == []


def real_row(day, month, raw, description):
    """A row as the game's own list gives them: the year is 1, and the list runs newest first."""
    return {"day": day, "month": month, "year": 1, "raw": str(raw), "description": description}


def test_the_games_list_runs_newest_first_and_is_turned_round():
    rows = [real_row(31, 7, 2602600, "Job Payment"), real_row(24, 7, -9959800, "Bluefang Piercer"),
            real_row(10, 5, 685700, "Job Payment")]
    entries = parse_entries(finance(rows))
    assert [e.date for e in entries] == ["0001-06-10", "0001-08-24", "0001-08-31"]  # oldest first now


def test_a_list_that_already_runs_oldest_first_is_left_alone():
    entries = parse_entries(finance([real_row(10, 5, 1, "a"), real_row(24, 7, 2, "b"), real_row(31, 7, 3, "c")]))
    assert [e.raw for e in entries] == [1, 2, 3]


def test_a_list_all_on_one_day_is_taken_as_newest_first_as_the_game_gives_it():
    entries = parse_entries(finance([real_row(31, 7, 3, "c"), real_row(31, 7, 2, "b"), real_row(31, 7, 1, "a")]))
    assert [e.raw for e in entries] == [1, 2, 3]


def test_successive_looks_at_the_games_newest_first_list_grow_the_history_the_right_way():
    ledger = Ledger()
    ledger.merge(finance([real_row(3, 7, 30, "Job Payment"), real_row(2, 7, 20, "Fuel"), real_row(1, 7, 10, "Job Payment")]))
    ledger.merge(finance([real_row(5, 7, 50, "Job Payment"), real_row(4, 7, -40, "Fuel"),
                          real_row(3, 7, 30, "Job Payment"), real_row(2, 7, 20, "Fuel")]))
    assert [e.raw for e in ledger.entries] == [10, 20, 30, -40, 50]  # oldest first, nothing twice


def test_kinds_name_the_game_s_job_payments():
    assert kind(Entry(1, 8, 31, 2602600, "Job Payment")) == "Contract payment"
    assert kind(Entry(1, 8, 24, -9959800, "Bluefang Piercer")) == "Spent"
    assert kind(Entry(1, 8, 24, 34900, "Circuit Breaker")) == "Earned"
