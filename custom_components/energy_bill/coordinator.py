"""Reads hourly statistics out of the recorder and turns them into a bill.

Statistics rather than states, deliberately. The recorder keeps hourly
long-term statistics for years — four of them on the machine this was written
for — while raw states are purged after days. Reading statistics means the
bill can be rebuilt for any past cycle without the integration having been
installed at the time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BONO_SOCIAL,
    CONF_CONSUMPTION,
    CONF_CYCLE_DAY,
    CONF_ENERGY_PRICE,
    CONF_ENERGY_PRICE_ENTITY,
    CONF_ENERGY_PRICE_TAXED,
    CONF_ENERGY_PRICE_P2,
    CONF_ENERGY_PRICE_P3,
    CONF_EXPORT,
    CONF_HOURLY_NETTING,
    CONF_METER_RENTAL,
    CONF_MONTHLY_FEE,
    CONF_MONTHLY_FEE_NAME,
    CONF_POWER_P1_KW,
    CONF_POWER_P1_PRICE,
    CONF_POWER_P2_KW,
    CONF_POWER_P2_PRICE,
    CONF_SURPLUS_CAPPED,
    CONF_SURPLUS_PRICE,
    CONF_SURPLUS_PRICE_ENTITY,
    CONF_SURPLUS_PRICE_TAXED,
    CONF_TAX_ELECTRICITY,
    CONF_TAX_INCLUDES_BONO,
    CONF_TAX_VAT,
    DEFAULT_CYCLE_DAY,
    DEFAULT_HOURLY_NETTING,
    DEFAULT_SURPLUS_CAPPED,
    DEFAULT_TAX_ELECTRICITY,
    DEFAULT_TAX_VAT,
    DOMAIN,
    ID_BONO,
    ID_ENERGY,
    ID_FEE,
    ID_IEE,
    ID_POWER,
    ID_RENTAL,
    ID_SURPLUS,
    ID_VAT,
)
from .engine import Hour, HourlySeries, Result, compute, forecast

_LOGGER = logging.getLogger(__name__)

# Once an hour is enough: the statistics themselves only move once an hour, so
# a shorter interval would re-read the same rows and change nothing.
UPDATE_INTERVAL = timedelta(minutes=30)


def cycle_bounds(now: datetime, day: int) -> tuple[datetime, datetime]:
    """Start and end of the billing cycle containing `now`, in local time.

    `day` is the day of the month the cycle starts on. Day 1 is the natural
    month, which is what most Spanish bills use; a retailer that bills on the
    15th is just a different number here.
    """
    day = max(1, min(28, int(day)))  # 28 so every month has the day
    start = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    if now < start:
        start = (start - timedelta(days=31)).replace(day=day)
    end = (start + timedelta(days=32)).replace(day=day)
    return start, end


class BillCoordinator(DataUpdateCoordinator):
    """Fetches statistics, runs the engine, and hands the result to the sensors."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN} {entry.title}", update_interval=UPDATE_INTERVAL
        )
        self.entry = entry

    @property
    def options(self) -> dict:
        """Entry data with the options layered on top, which is where edits land."""
        return {**self.entry.data, **self.entry.options}

    def engine_config(self, series: dict[str, HourlySeries] | None = None) -> dict:
        """Translate the flat form fields into the engine's concept model.

        `series` carries hourly prices already read from the price entities, if
        any were configured. An entity beats a fixed number: someone who picked
        one meant it.
        """
        o = self.options
        series = series or {}
        energy_price = float(o.get(CONF_ENERGY_PRICE, 0.0))
        p2 = o.get(CONF_ENERGY_PRICE_P2)
        p3 = o.get(CONF_ENERGY_PRICE_P3)
        # Only build a per-period price when the user actually set one. A flat
        # tariff is the common case and deserves the simpler shape.
        if "energy" in series:
            price: object = series["energy"]
        elif p2 is not None and p3 is not None:
            price = {"P1": energy_price, "P2": float(p2), "P3": float(p3)}
        else:
            price = energy_price

        iee_over = [ID_POWER, ID_ENERGY, ID_SURPLUS]
        if o.get(CONF_TAX_INCLUDES_BONO):
            iee_over.append(ID_BONO)

        daily = []
        if o.get(CONF_BONO_SOCIAL):
            daily.append({"id": ID_BONO, "price": float(o[CONF_BONO_SOCIAL])})
        if o.get(CONF_METER_RENTAL):
            daily.append({"id": ID_RENTAL, "price": float(o[CONF_METER_RENTAL])})
        monthly = []
        if o.get(CONF_MONTHLY_FEE):
            monthly.append(
                # The only name the engine ever carries: the one the user typed.
                {
                    "id": ID_FEE,
                    "name": o.get(CONF_MONTHLY_FEE_NAME) or None,
                    "price": float(o[CONF_MONTHLY_FEE]),
                }
            )

        vat_over = [ID_POWER, ID_ENERGY, ID_SURPLUS, ID_BONO, ID_RENTAL, ID_FEE, ID_IEE]
        return {
            "energy_price": price,
            "surplus_price": series.get("surplus", float(o.get(CONF_SURPLUS_PRICE, 0.0))),
            "surplus_capped_at_energy": bool(o.get(CONF_SURPLUS_CAPPED, DEFAULT_SURPLUS_CAPPED)),
            "hourly_netting": bool(o.get(CONF_HOURLY_NETTING, DEFAULT_HOURLY_NETTING)),
            "power": [
                {"kw": float(o.get(CONF_POWER_P1_KW, 0)), "price": float(o.get(CONF_POWER_P1_PRICE, 0))},
                {"kw": float(o.get(CONF_POWER_P2_KW, 0)), "price": float(o.get(CONF_POWER_P2_PRICE, 0))},
            ],
            "daily": daily,
            "monthly": monthly,
            "taxes": [
                {
                    "id": ID_IEE,
                    "percent": float(o.get(CONF_TAX_ELECTRICITY, DEFAULT_TAX_ELECTRICITY)),
                    "over": iee_over,
                },
                {
                    "id": ID_VAT,
                    "percent": float(o.get(CONF_TAX_VAT, DEFAULT_TAX_VAT)),
                    "over": vat_over,
                },
            ],
        }

    async def _hourly(self, start: datetime, end: datetime) -> list[Hour]:
        """Hourly kWh in and out, derived from the statistics' running totals."""
        o = self.options
        ids = [o[CONF_CONSUMPTION]]
        export_id = o.get(CONF_EXPORT)
        if export_id:
            ids.append(export_id)

        # One hour before the cycle, on purpose. The consumption of an hour is
        # the step between its running total and the previous one, so without
        # that earlier row the first hour of every cycle would be dropped —
        # about a kWh a month, silently.
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start - timedelta(hours=1),
            end,
            set(ids),
            "hour",
            None,
            {"sum"},
        )

        def series(entity_id: str) -> dict[datetime, float]:
            """Turn the cumulative `sum` into per-hour deltas.

            Statistics for a total_increasing sensor carry a running total, so
            the consumption of an hour is the step between it and the one
            before. A meter reset shows up as a negative step and is dropped
            rather than counted as a huge negative consumption.
            """
            rows = stats.get(entity_id) or []
            out: dict[datetime, float] = {}
            previous: float | None = None
            for row in rows:
                total = row.get("sum")
                if total is None:
                    continue
                when = dt_util.as_local(dt_util.utc_from_timestamp(row["start"]))
                if previous is not None and when >= start:
                    step = total - previous
                    out[when] = step if step >= 0 else 0.0
                previous = total
            return out

        # A consumption sensor with no statistics at all is not a quiet zero:
        # it is a broken setup, and a bill of 0,00 € looks plausible enough that
        # nobody would ever question it. Say so instead.
        if not (stats.get(o[CONF_CONSUMPTION]) or []):
            raise UpdateFailed(
                f"{o[CONF_CONSUMPTION]} has no hourly statistics for this cycle. "
                "The bill is built from long-term statistics, so the sensor needs "
                "a state_class that produces them."
            )

        imported = series(o[CONF_CONSUMPTION])
        exported = series(export_id) if export_id else {}
        moments = sorted(set(imported) | set(exported))
        return [Hour(m, imported.get(m, 0.0), exported.get(m, 0.0)) for m in moments]

    async def _price_series(self, start: datetime, end: datetime) -> dict[str, HourlySeries]:
        """Hourly prices from the configured price entities, if there are any.

        The mean of each hour, not the current state: a bill is built from what
        the price WAS while the energy flowed, and reading `states()` today
        would apply tonight's price to the whole month.
        """
        o = self.options
        wanted = {
            "energy": o.get(CONF_ENERGY_PRICE_ENTITY),
            "surplus": o.get(CONF_SURPLUS_PRICE_ENTITY),
        }
        taxed = {
            "energy": bool(o.get(CONF_ENERGY_PRICE_TAXED)),
            "surplus": bool(o.get(CONF_SURPLUS_PRICE_TAXED)),
        }
        # A price that already carries taxes has to be stripped back before the
        # engine applies them again, or every kWh is taxed twice. The factor is
        # the two taxes compounded, which is exactly how the retailer built it:
        # checked against Octopus, whose sensor reads 0,153045 with taxes and
        # publishes 0,120331 without — and 0,153045 ÷ 1,271864 is 0,120331.
        factor = (
            (1 + float(o.get(CONF_TAX_ELECTRICITY, DEFAULT_TAX_ELECTRICITY)) / 100)
            * (1 + float(o.get(CONF_TAX_VAT, DEFAULT_TAX_VAT)) / 100)
        )
        ids = {v for v in wanted.values() if v}
        if not ids:
            return {}

        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, end, ids, "hour", None, {"mean"}
        )
        out: dict[str, HourlySeries] = {}
        for key, entity_id in wanted.items():
            if not entity_id:
                continue
            rows = stats.get(entity_id) or []
            divisor = factor if taxed[key] else 1.0
            by_hour = {
                dt_util.as_local(dt_util.utc_from_timestamp(r["start"])): float(r["mean"]) / divisor
                for r in rows
                if r.get("mean") is not None
            }
            if not by_hour:
                # No statistics yet: better the price showing right now than zero.
                state = self.hass.states.get(entity_id)
                try:
                    fallback = (float(state.state) if state else 0.0) / (
                        factor if taxed[key] else 1.0
                    )
                except (TypeError, ValueError):
                    fallback = 0.0
                out[key] = HourlySeries({}, fallback)
                continue
            # Gaps carry the last known price forward rather than taking the
            # period average. A tariff holds its price until it changes, so the
            # hour before a gap is a far better guess than the mean of the
            # month — and with an indexed tariff the mean is wrong by design,
            # because prices cluster by time of day.
            relleno = dict(by_hour)
            ultimo: float | None = None
            hora = start.replace(minute=0, second=0, microsecond=0)
            while hora <= end:
                if hora in relleno:
                    ultimo = relleno[hora]
                elif ultimo is not None:
                    relleno[hora] = ultimo
                hora += timedelta(hours=1)
            media = sum(by_hour.values()) / len(by_hour)
            out[key] = HourlySeries(relleno, media)
        return out

    async def _async_update_data(self) -> dict:
        now = dt_util.now()
        # The day arrives from a dropdown, so it is a string.
        start, end = cycle_bounds(now, int(self.options.get(CONF_CYCLE_DAY, DEFAULT_CYCLE_DAY)))
        config = self.engine_config(await self._price_series(start, now))

        hours = await self._hourly(start, now)
        elapsed = max((now - start).total_seconds() / 86400, 1 / 24)
        result: Result = compute(hours, config, days=elapsed)

        resumen: dict[str, dict] = {}
        for clave in ("energy_price", "surplus_price"):
            valor = config.get(clave)
            if hasattr(valor, "for_hour"):
                precios = list(valor.by_hour.values())
                resumen[clave] = {
                    "source": "entity",
                    "hours_with_price": len(precios),
                    "min": round(min(precios), 6) if precios else None,
                    "max": round(max(precios), 6) if precios else None,
                    "fallback": round(valor.default, 6),
                }
            else:
                resumen[clave] = {"source": "fixed", "value": valor}

        cycle_days = (end - start).days
        return {
            "prices": resumen,
            "engine_config": {
                k: v for k, v in config.items()
                if k not in ("energy_price", "surplus_price")
            },
            "result": result,
            "forecast": forecast(result, cycle_days, config),
            "cycle_start": start,
            "cycle_end": end,
            "cycle_days": cycle_days,
            "elapsed_days": elapsed,
        }
