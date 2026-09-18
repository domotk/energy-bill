"""Turns hourly kWh into a Spanish electricity bill.

No Home Assistant imports here either: the engine takes plain numbers and a
plain dict, so the whole thing can be checked against a real paper bill.

The shape of the model comes from reading three real bills side by side — one
from Octopus and two from Iberdrola — and it is built around the thing that
actually differs between them: **which concepts each tax applies to**. Octopus
leaves the bono social out of the electricity-tax base; Iberdrola puts it in.
That is not a rounding detail, it is a different number, so `over` is a list of
concept ids rather than something hard-coded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from .periods import energy_period, holidays_for

# Concepts are summed into a breakdown under these keys. The ids are stable and
# are what a tax's `over` list refers to.
POWER = "power"
ENERGY = "energy"
SURPLUS = "surplus"


class HourlySeries:
    """A price that changed hour by hour, as read from a sensor's statistics.

    A fixed number cannot express an indexed tariff, and a per-period mapping
    cannot either: PVPC moves every hour, not every period. Hours with no
    reading fall back to `default`, so a gap in the recorder costs the average
    rather than zero.
    """

    def __init__(self, by_hour: dict[datetime, float], default: float = 0.0) -> None:
        self.by_hour = by_hour
        self.default = default

    def for_hour(self, when: datetime) -> float:
        return self.by_hour.get(when, self.default)


@dataclass
class Hour:
    """One hour of the billing period, already in local time.

    `submeters` carries kWh measured by something behind the main meter — a car
    charger, typically — keyed by the submeter id.
    """

    start: datetime
    imported_kwh: float
    exported_kwh: float
    submeters: dict[str, float] = field(default_factory=dict)


@dataclass
class Result:
    """Amounts keyed by concept id.

    Ids only, never display names: the engine has no business knowing which
    language anybody reads. Home Assistant resolves them through the usual
    translation files, and a concept the user named themselves keeps the name
    they gave it.
    """

    concepts: dict[str, float] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    taxes: dict[str, float] = field(default_factory=dict)
    energy_by_period: dict[str, float] = field(default_factory=dict)
    submeter_kwh: dict[str, float] = field(default_factory=dict)
    imported_kwh: float = 0.0
    exported_kwh: float = 0.0
    days: float = 0.0

    @property
    def subtotal(self) -> float:
        return sum(self.concepts.values())

    @property
    def total(self) -> float:
        return round(self.subtotal + sum(self.taxes.values()), 2)


def cents(value: float) -> float:
    """Round to cents, half up. For display only — never mid-calculation.

    Three real bills were checked against every plausible rounding order and no
    single one reproduces all of them: Octopus prints its energy split per
    period and rounds each line, yet computes its taxes on the unrounded base;
    Iberdrola bills energy as one blended line and works either way. Rounding
    per period fixes Octopus and breaks Iberdrola, and vice versa.

    So the engine keeps full precision throughout and rounds once, at the edge.
    Expect the total to sit within a cent or two of paper. Chasing that last
    cent would mean modelling each retailer's rounding order from a sample of
    three, which is overfitting, not accuracy.
    """
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _price_for(price, period: str, when: datetime | None = None) -> float:
    """A price is one of three shapes, in rising order of flexibility.

    A plain number; a mapping per tariff period (P1/P2/P3); or a list of time
    windows. The windows exist for EV tariffs: a retailer sells a cheaper kWh
    between, say, 01:00 and 07:00, and that boundary has nothing to do with the
    regulated P1/P2/P3 split. A window may narrow itself to certain weekdays.
    """
    if hasattr(price, "for_hour"):
        return float(price.for_hour(when)) if when is not None else float(price.default)
    if isinstance(price, list):
        if when is not None:
            for w in price:
                if "from" not in w:      # the catch-all entry, tried last
                    continue
                if "weekdays" in w and when.weekday() not in w["weekdays"]:
                    continue
                start, end = int(w["from"]), int(w["to"])
                inside = start <= when.hour < end if start < end else (when.hour >= start or when.hour < end)
                if inside:
                    return float(w["price"])
        return float(next((w["price"] for w in price if "from" not in w), 0.0))
    if isinstance(price, dict):
        return float(price.get(period, 0.0))
    return float(price)


def compute(hours: list[Hour], config: dict, days: float | None = None) -> Result:
    """Build the bill from hourly readings.

    Full precision throughout; rounded only for display. See `cents` for why,
    and for the size of the residual disagreement with paper.

    `days` is passed in rather than derived from `hours` because the daily and
    monthly concepts accrue over the billing period even for hours where no
    statistics exist — a gap in the recorder must not make the meter rental
    cheaper.
    """
    res = Result()
    res.days = float(days if days is not None else len(hours) / 24)

    net_hourly = bool(config.get("hourly_netting", True))
    years = {h.start.year for h in hours} or {date.today().year}
    holidays = holidays_for(years)

    # --- energy, hour by hour -------------------------------------------
    # Netting happens inside the hour because that is how Spanish surplus
    # compensation works: export three and import one in the same hour and you
    # have exported two, not both.
    energy_by_period_cost: dict[str, float] = {}
    surplus_credit = 0.0
    prices = config.get("energy_price", 0.0)
    surplus_price = float(config.get("surplus_price", 0.0))

    submeters = config.get("submeters", [])
    sub_kwh: dict[str, float] = {s["id"]: 0.0 for s in submeters}
    sub_cost: dict[str, float] = {s["id"]: 0.0 for s in submeters}

    for h in hours:
        imported, exported = h.imported_kwh, h.exported_kwh
        # A submeter sits behind the main meter, so its kWh are already inside
        # `imported`. Charging them at their own price means taking them out of
        # the general energy first, or they would be billed twice.
        for s in submeters:
            used = float(h.submeters.get(s["id"], 0.0))
            if not used:
                continue
            used = min(used, imported) if s.get("behind_main", True) else used
            sub_kwh[s["id"]] += used
            sub_cost[s["id"]] += used * _price_for(s["price"], energy_period(h.start, holidays), h.start)
            if s.get("behind_main", True):
                imported -= used
        if net_hourly:
            net = imported - exported
            imported, exported = max(net, 0.0), max(-net, 0.0)
        period = energy_period(h.start, holidays)
        res.energy_by_period[period] = res.energy_by_period.get(period, 0.0) + imported
        res.imported_kwh += imported
        res.exported_kwh += exported
        energy_by_period_cost[period] = (
            energy_by_period_cost.get(period, 0.0)
            + imported * _price_for(prices, period, h.start)
        )
        surplus_credit += exported * surplus_price

    energy_cost = sum(energy_by_period_cost.values())

    # The surplus can wipe out the energy term but never turn it into money the
    # retailer owes you: anything beyond that is carried by the virtual battery,
    # if the contract has one, and never by this bill.
    if config.get("surplus_capped_at_energy", True):
        surplus_credit = min(surplus_credit, energy_cost)

    res.concepts[ENERGY] = energy_cost

    if surplus_credit:
        res.concepts[SURPLUS] = -surplus_credit


    # --- submeters as their own concepts ---------------------------------
    for s in submeters:
        if sub_kwh[s["id"]]:
            res.concepts[s["id"]] = sub_cost[s["id"]]
            res.names[s["id"]] = s.get("name", s["id"])
            res.submeter_kwh[s["id"]] = sub_kwh[s["id"]]

    # --- power: a flat daily charge per power period ---------------------
    power_total = 0.0
    for entry in config.get("power", []):
        power_total += float(entry["kw"]) * float(entry["price"]) * res.days
    if power_total:
        res.concepts[POWER] = power_total


    # --- fixed concepts --------------------------------------------------
    # Ids are arbitrary and chosen by whoever writes the config: they exist only
    # so a tax's `over` can name a concept. `name` is what a person reads.
    for entry in config.get("daily", []):
        res.concepts[entry["id"]] = float(entry["price"]) * res.days
        if entry.get("name"):
            res.names[entry["id"]] = entry["name"]
    for entry in config.get("monthly", []):
        res.concepts[entry["id"]] = float(entry["price"]) * float(entry.get("months", 1))
        if entry.get("name"):
            res.names[entry["id"]] = entry["name"]

    # --- discounts -------------------------------------------------------
    # Computed on the base BEFORE any discount, and stacked. Iberdrola's
    # "Plan Estable" applies 15 % and 5 % both against the undiscounted energy,
    # which is 20 % of it, not 19.25 %.
    for entry in config.get("discounts", []):
        base = sum(res.concepts.get(i, 0.0) for i in entry["over"])
        res.concepts[entry["id"]] = -base * float(entry["percent"]) / 100
        if entry.get("name"):
            res.names[entry["id"]] = entry["name"]

    # --- taxes -----------------------------------------------------------
    # Order matters: a tax may sit in a later tax's base, which is exactly what
    # IVA does with the electricity tax.
    for entry in config.get("taxes", []):
        base = 0.0
        for i in entry["over"]:
            base += res.concepts.get(i, res.taxes.get(i, 0.0))
        res.taxes[entry["id"]] = base * float(entry["percent"]) / 100

    return res


def forecast(res: Result, days_in_cycle: float, config: dict) -> float:
    """Project the cycle's total from what has accrued so far.

    Only the consumption-driven part is extrapolated. The power term and the
    daily charges are already known for the whole cycle — they depend on the
    calendar, not on behaviour — so scaling them would invent uncertainty where
    there is none.
    """
    if res.days <= 0:
        return 0.0
    return compute_projection(res, days_in_cycle / res.days, days_in_cycle, config)


def compute_projection(res: Result, ratio: float, days_in_cycle: float, config: dict) -> float:
    """Rebuild the totals with energy scaled and the calendar terms at full cycle."""
    concepts: dict[str, float] = {}
    for key, value in res.concepts.items():
        if key in (ENERGY, SURPLUS) or key.startswith("discount"):
            concepts[key] = value * ratio
        else:
            concepts[key] = value / res.days * days_in_cycle if res.days else 0.0
    taxes: dict[str, float] = {}
    for entry in config.get("taxes", []):
        base = sum(concepts.get(i, taxes.get(i, 0.0)) for i in entry["over"])
        taxes[entry["id"]] = base * float(entry["percent"]) / 100
    return round(sum(concepts.values()) + sum(taxes.values()), 2)
