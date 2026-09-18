"""Which tariff period an hour falls in, under the Spanish 2.0TD and 3.0TD access tariffs.

This lives on its own, with no Home Assistant imports, so it can be tested
against a real bill without starting a server.

The rule that surprises people: weekends and national holidays are valley all
day, whatever the clock says. A bill computed without that reads too high every
Saturday and Sunday, which is a third of the year.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# --- the periods themselves ---------------------------------------------
# 2.0TD splits ENERGY into three periods and POWER into two. They are not the
# same split and must not be conflated: the power term is a flat charge per day
# for each of its two periods, so it needs no hour-by-hour classification at
# all — a bill reads "4,60 kW x 31 días" for punta and again for valle.
P1, P2, P3 = "P1", "P2", "P3"

# Peninsular local hours. Everything outside these is valley.
_PEAK_HOURS = frozenset([10, 11, 12, 13, 18, 19, 20, 21])
_FLAT_HOURS = frozenset([8, 9, 14, 15, 16, 17, 22, 23])


def easter_sunday(year: int) -> date:
    """Gregorian Easter (Meeus/Jones/Butcher). Good Friday is two days earlier.

    Needed because Viernes Santo is the one national holiday that moves, and a
    hard-coded table would quietly go stale the year nobody updates it.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return date(year, month, day + 1)


def national_holidays(year: int) -> frozenset[date]:
    """Holidays that count nationwide for the tariff calendar.

    Deliberately only the national ones. Regional and local holidays do NOT
    make a day valley for billing purposes, however much the shops are shut.
    """
    fixed = [(1, 1), (1, 6), (5, 1), (8, 15), (10, 12), (11, 1), (12, 6), (12, 8), (12, 25)]
    days = {date(year, m, d) for m, d in fixed}
    days.add(easter_sunday(year) - timedelta(days=2))  # Viernes Santo
    return frozenset(days)


def is_valley_day(when: date, holidays: frozenset[date] | None = None) -> bool:
    """Saturday, Sunday or a national holiday: valley from midnight to midnight."""
    if when.weekday() >= 5:
        return True
    return when in (holidays if holidays is not None else national_holidays(when.year))


def energy_period(when: datetime, holidays: frozenset[date] | None = None) -> str:
    """The energy period of a local-time hour under 2.0TD."""
    if is_valley_day(when.date(), holidays):
        return P3
    if when.hour in _PEAK_HOURS:
        return P1
    if when.hour in _FLAT_HOURS:
        return P2
    return P3


def holidays_for(years) -> frozenset[date]:
    """Precomputed holidays across a span of years, so a month-long loop asks once."""
    out: set[date] = set()
    for year in years:
        out |= national_holidays(year)
    return frozenset(out)
