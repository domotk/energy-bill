"""The engine, checked against three real Spanish bills.

The bills are the specification: if the engine disagrees with paper, the engine
is wrong. The three were chosen because they disagree with each other in ways
that a single bill would never reveal — one itemises energy per tariff period
and another bills it at a single blended price, one puts the bono social inside
the electricity-tax base and another leaves it out, and one stacks two
percentage discounts on top of the undiscounted energy.

They belong to real households, so nothing identifying is kept: no names, no
dates, no meter readings. Only the arithmetic, which is the part under test.

Run with: python tests/test_engine.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
from datetime import date, datetime, timedelta

RAIZ = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "energy_bill"


def cargar():
    """Load the engine without importing the package.

    The package's `__init__` imports Home Assistant, which is not installed in
    CI and has no business being a dependency of a pure arithmetic test. The
    engine was written free of Home Assistant precisely so this works.
    """
    if "eb" not in sys.modules:
        paquete = types.ModuleType("eb")
        paquete.__path__ = [str(RAIZ)]
        sys.modules["eb"] = paquete
    modulos = {}
    for nombre in ("periods", "engine"):
        spec = importlib.util.spec_from_file_location(f"eb.{nombre}", RAIZ / f"{nombre}.py")
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[f"eb.{nombre}"] = modulo
        spec.loader.exec_module(modulo)
        modulos[nombre] = modulo
    return modulos["engine"], modulos["periods"]


engine, periods = cargar()
Hour, compute = engine.Hour, engine.compute
easter_sunday = periods.easter_sunday
energy_period = periods.energy_period
national_holidays = periods.national_holidays

fallos: list[str] = []


# Two cents of tolerance, deliberately. No single rounding order reproduces all
# three bills: one retailer prints energy split per period and rounds each line
# while computing its taxes on the unrounded base, another bills energy as one
# blended line and works either way. See `cents` in engine.py.
def check(nombre: str, obtenido: float, esperado: float, tol: float = 0.02) -> None:
    ok = abs(obtenido - esperado) <= tol
    print(f"  {'OK ' if ok else 'NO '} {nombre:<44} {obtenido:>9.2f}  esperado {esperado:>9.2f}")
    if not ok:
        fallos.append(nombre)


def igual(nombre: str, obtenido, esperado) -> None:
    ok = obtenido == esperado
    print(f"  {'OK ' if ok else 'NO '} {nombre:<44} {obtenido}")
    if not ok:
        fallos.append(nombre)


print("════ tariff calendar ════")
for año, esperado in ((2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5))):
    igual(f"Easter {año}", easter_sunday(año), esperado)

fest = national_holidays(2026)
igual("ten national holidays in 2026", len(fest), 10)
igual("Good Friday among them", easter_sunday(2026) - timedelta(days=2) in fest, True)

for cuando, esperado, texto in (
    (datetime(2026, 9, 16, 11), "P1", "Wednesday 11:00 is peak"),
    (datetime(2026, 9, 16, 9), "P2", "Wednesday 09:00 is mid-peak"),
    (datetime(2026, 9, 16, 3), "P3", "Wednesday 03:00 is off-peak"),
    (datetime(2026, 9, 19, 11), "P3", "Saturday 11:00 is off-peak all day"),
    (datetime(2026, 12, 25, 11), "P3", "Christmas 11:00 is off-peak"),
    (datetime(2026, 4, 3, 11), "P3", "Good Friday 11:00 is off-peak"),
):
    igual(texto, energy_period(cuando), esperado)


def horas(kwh_import, kwh_export, n=744, inicio=datetime(2026, 8, 1)):
    """Spread a total over n hours. With a flat price the split cannot matter."""
    return [Hour(inicio + timedelta(hours=i), kwh_import / n, kwh_export / n) for i in range(n)]


IVA = {"id": "vat", "percent": 21}

print("\n════ case A · flat price, energy itemised per period, solar export ════")
# The retailer leaves the bono social OUT of the electricity-tax base.
cfg_a = {
    "energy_price": 0.120331,
    "surplus_price": 0.05,
    "hourly_netting": False,
    "power": [{"kw": 4.60, "price": 0.09537}, {"kw": 4.60, "price": 0.02518}],
    "daily": [
        {"id": "bono_social", "price": 0.02484},
        {"id": "meter_rental", "price": 0.02677},
    ],
    "taxes": [
        {"id": "iee", "percent": 5.1127, "over": ["power", "energy", "surplus"]},
        {**IVA, "over": ["power", "energy", "surplus", "bono_social", "meter_rental", "iee"]},
    ],
}
# Real per-period consumption: Monday 11:00 is peak, 09:00 mid-peak, 03:00 off-peak.
h_a = [
    Hour(datetime(2026, 8, 3, 11), 57.71, 0.0),
    Hour(datetime(2026, 8, 3, 9), 56.02, 0.0),
    Hour(datetime(2026, 8, 3, 3), 228.32, 0.0),
    Hour(datetime(2026, 8, 4, 3), 0.0, 525.47),
]
r = compute(h_a, cfg_a, days=31)
check("energy in P1 (kWh)", r.energy_by_period["P1"], 57.71, tol=0.001)
check("energy in P3 (kWh)", r.energy_by_period["P3"], 228.32, tol=0.001)
check("power", r.concepts["power"], 17.19)
check("energy", r.concepts["energy"], 41.15)
check("surplus", r.concepts["surplus"], -26.27)
check("bono social", r.concepts["bono_social"], 0.77)
check("meter rental", r.concepts["meter_rental"], 0.83)
check("electricity tax", r.taxes["iee"], 1.64)
check("VAT", r.taxes["vat"], 7.41)
check("TOTAL", r.total, 42.72)

print("\n════ case B · single blended energy price, bono social inside the tax base ════")
cfg_b = {
    "energy_price": 0.16716,
    "surplus_price": 0.06,
    "hourly_netting": False,
    "power": [
        {"kw": 7.5, "price": 27.70 / (7.5 * 31)},
        {"kw": 7.5, "price": 17.19 / (7.5 * 31)},
    ],
    "daily": [
        {"id": "bono_social", "price": 0.77 / 31},
        {"id": "meter_rental", "price": 0.02663},
    ],
    "taxes": [
        # This is the difference from case A, and it is worth real money.
        {"id": "iee", "percent": 5.11269632,
         "over": ["power", "energy", "surplus", "bono_social"]},
        {**IVA, "over": ["power", "energy", "surplus", "bono_social", "meter_rental", "iee"]},
    ],
}
r = compute(horas(731.94, 226.96), cfg_b, days=31)
check("power", r.concepts["power"], 44.89)
check("energy", r.concepts["energy"], 122.35)
check("surplus", r.concepts["surplus"], -13.62)
check("electricity tax", r.taxes["iee"], 7.89)
check("VAT", r.taxes["vat"], 34.25)
check("TOTAL", r.total, 197.36)

print("\n════ case C · two stacked discounts and a monthly fee ════")
cfg_c = {
    "energy_price": 248.50 / 1411.07,
    "surplus_price": 0.06,
    "hourly_netting": False,
    "power": [
        {"kw": 13.8, "price": 46.28 / (13.8 * 31)},
        {"kw": 13.8, "price": 19.91 / (13.8 * 31)},
    ],
    "daily": [
        {"id": "bono_social", "price": 0.77 / 31},
        {"id": "meter_rental", "price": 1.39 / 31},
    ],
    "monthly": [{"id": "service_pack", "name": "Service pack", "price": 8.95, "months": 1}],
    # Both percentages apply to the UNDISCOUNTED energy, so together they take
    # 20 % off and not 19.25 %.
    "discounts": [
        {"id": "discount_15", "percent": 15, "over": ["energy"]},
        {"id": "discount_5", "percent": 5, "over": ["energy"]},
    ],
    "taxes": [
        {"id": "iee", "percent": 5.11269632,
         "over": ["power", "energy", "surplus", "discount_15", "discount_5", "bono_social"]},
        {**IVA, "over": ["power", "energy", "surplus", "discount_15", "discount_5",
                         "bono_social", "meter_rental", "service_pack", "iee"]},
    ],
}
r = compute(horas(1411.07, 41.14), cfg_c, days=31)
check("power", r.concepts["power"], 66.19)
check("energy", r.concepts["energy"], 248.50)
check("15 % discount", r.concepts["discount_15"], -37.28)
check("5 % discount", r.concepts["discount_5"], -12.43)
check("surplus", r.concepts["surplus"], -2.47)
check("service pack", r.concepts["service_pack"], 8.95)
check("electricity tax", r.taxes["iee"], 13.46)
check("VAT", r.taxes["vat"], 60.29)
check("TOTAL", r.total, 347.37)

print("\n════ hourly netting · the case no bill covers ════")
# Export three and import one within the same hour and you exported two. No
# paper bill shows this working, so it is checked directly.
cfg_n = dict(cfg_a, hourly_netting=True)
r = compute(
    [
        Hour(datetime(2026, 8, 3, 12), 1.0, 3.0),
        Hour(datetime(2026, 8, 3, 13), 3.0, 1.0),
    ],
    cfg_n,
    days=1,
)
check("net imported (kWh)", r.imported_kwh, 2.0, tol=0.001)
check("net exported (kWh)", r.exported_kwh, 2.0, tol=0.001)

print("\n════ virtual battery · a balance, not a discount ════")
# Solar Wallet, Solar Cloud: euros that surplus turned into once it had nothing
# left to cancel out. Spent after tax, against the finished bill.
r = compute(h_a, dict(cfg_a, credits=[{"id": "virtual_battery", "amount": 20.0}]), days=31)
check("the bill before the balance is untouched", r.gross, 42.72)
check("and what is left to pay", r.total, 22.72)

r = compute(h_a, dict(cfg_a, credits=[{"id": "virtual_battery", "amount": 1000.0}]), days=31)
check("a balance larger than the bill pays all of it", r.credits["virtual_battery"], -42.72)
check("and leaves nothing to pay, not money owed", r.total, 0.0)

print("\n════ electric vehicle ════")
sin_extras = {"surplus_price": 0.0, "hourly_netting": False, "power": [], "daily": [], "taxes": []}

cfg_v = dict(sin_extras, energy_price=[{"from": 1, "to": 7, "price": 0.068}, {"price": 0.17}])
r = compute(
    [Hour(datetime(2026, 9, 16, 3), 10.0, 0.0), Hour(datetime(2026, 9, 16, 12), 10.0, 0.0)],
    cfg_v,
    days=1,
)
check("cheap window applies only inside it", r.concepts["energy"], 10 * 0.068 + 10 * 0.17)

cfg_lv = dict(
    sin_extras,
    energy_price=[
        {"from": 1, "to": 7, "weekdays": [0, 1, 2, 3, 4], "price": 0.068},
        {"price": 0.17},
    ],
)
r = compute([Hour(datetime(2026, 9, 19, 3), 10.0, 0.0)], cfg_lv, days=1)
check("weekday-only window skips Saturday", r.concepts["energy"], 10 * 0.17)

# A charger sits behind the main meter, so its kWh are already counted once.
cfg_ev = dict(
    sin_extras,
    energy_price=0.17,
    submeters=[{"id": "ev", "name": "Electric vehicle", "price": 0.068, "behind_main": True}],
)
r = compute([Hour(datetime(2026, 9, 16, 3), 12.0, 0.0, {"ev": 10.0})], cfg_ev, days=1)
check("house pays for what the car did not", r.concepts["energy"], 2 * 0.17)
check("car pays its own price", r.concepts["ev"], 10 * 0.068)
check("nothing billed twice", r.subtotal, 2 * 0.17 + 10 * 0.068)

r = compute([Hour(datetime(2026, 9, 16, 3), 4.0, 0.0, {"ev": 10.0})], cfg_ev, days=1)
check("submeter capped at the main meter", r.submeter_kwh["ev"], 4.0, tol=0.001)

print("\n" + ("  ALL GOOD" if not fallos else f"  {len(fallos)} FAILED: {fallos}"))
sys.exit(1 if fallos else 0)
