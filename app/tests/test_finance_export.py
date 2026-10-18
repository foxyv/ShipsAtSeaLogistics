"""What the mod writes about the company's money: the game's ledger (a date, an amount and a description each)
and the capital, from the game's FinanceComponent."""

import pytest

from tests.lua_harness import FakeArray, FakeText, GameObject, Vec, load_mod


def amount(raw):
    return Vec(Value=raw)


def transaction(day, month, year, raw, description):
    return Vec(Date=Vec(Day=Vec(Day=day), Month=month, Year=Vec(Year=year)), Value=amount(raw),
               Description=FakeText(description))


class FakeFinance(GameObject):
    def __init__(self, owner_class, transactions, capital_raw, history=100):
        super().__init__("FinanceComponent")
        self.owner = GameObject(owner_class)
        self.Ledger = Vec(Transactions=FakeArray(transactions), TransactionHistorySize=history)
        self.Capital = amount(capital_raw)

    def GetOwner(self):
        return self.owner


class FakeDecimalLibrary:
    """The game's own conversion of a decimal to a number: here the unit is hundredths."""

    def Conv_DecimalToFloat(self, decimal):
        return decimal.Value / 100.0


@pytest.fixture
def mod():
    return load_mod()


def install(mod, components, library=True):
    mod.g.Components = mod.table(components)
    mod.g.Library = FakeDecimalLibrary() if library else None
    mod.lua.execute("""
        function FindAllOf(name) if name == 'FinanceComponent' then return Components end end
        function StaticFindObject(path) if path:find('DecimalMathLibrary') then return Library end end
    """)


def test_the_players_ledger_is_exported_with_dates_amounts_and_descriptions(mod):
    ledger = FakeFinance("BP_SasPlayerState_C", [
        transaction(3, 9, 1820, 1250050, "Mission completed: Fish to Fenes"),
        transaction(4, 9, 1820, -30000, "Port fee"),
    ], capital_raw=987600)
    install(mod, [ledger])
    finance = mod.test.ExportFinance()
    assert finance["capitalRaw"] == "987600" and finance["capital"] == 9876.0
    assert finance["historySize"] == 100 and finance["owner"] == "BP_SasPlayerState_C"
    rows = [finance["transactions"][i] for i in (1, 2)]
    assert [(r["day"], r["month"], r["year"], r["raw"], r["description"]) for r in rows] == [
        (3, 9, 1820, "1250050", "Mission completed: Fish to Fenes"),
        (4, 9, 1820, "-30000", "Port fee"),
    ]


def test_the_players_ledger_is_picked_from_several(mod):
    other = FakeFinance("BP_Merchant_C", [transaction(1, 0, 1820, 5, "x")] * 5, capital_raw=1)
    mine = FakeFinance("BP_SasPlayerState_C", [transaction(1, 0, 1820, 7, "y")], capital_raw=2)
    install(mod, [other, mine])
    finance = mod.test.ExportFinance()
    assert finance["owner"] == "BP_SasPlayerState_C" and finance["capitalRaw"] == "2"


def test_without_the_games_conversion_the_raw_amounts_are_still_exported(mod):
    install(mod, [FakeFinance("BP_SasPlayerState_C", [transaction(1, 0, 1820, 100, "z")], capital_raw=100)], library=False)
    finance = mod.test.ExportFinance()
    assert finance["capital"] is None and finance["capitalRaw"] == "100"
    assert finance["transactions"][1]["raw"] == "100"


def test_no_finance_component_means_no_finance_section(mod):
    install(mod, [])
    assert mod.test.ExportFinance() is None
