"""What to hand over when a bill does not match the paper one.

A bill is a chain of numbers, and the only useful question is where the chain
first diverges. So this dumps every link: the tariff as configured, what came
out of the statistics, what the engine made of it, and — the part that is
usually the culprit — how many hours actually had data.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import BillCoordinator
from .engine import cents

# Entity ids are not secrets and are the first thing anyone needs to see; there
# is nothing in this config worth hiding. Kept explicit so that adding a field
# that IS sensitive later is a decision rather than an oversight.
TO_REDACT: set[str] = set()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: BillCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    result = data.get("result")

    salida: dict[str, Any] = {
        "config": async_redact_data({**entry.data, **entry.options}, TO_REDACT),
        "cycle": {
            "start": data["cycle_start"].isoformat() if data.get("cycle_start") else None,
            "end": data["cycle_end"].isoformat() if data.get("cycle_end") else None,
            "days_in_cycle": data.get("cycle_days"),
            "days_elapsed": round(data["elapsed_days"], 3) if data.get("elapsed_days") else None,
        },
        "update": {
            "last_success": coordinator.last_update_success,
            "interval_minutes": coordinator.update_interval.total_seconds() / 60
            if coordinator.update_interval
            else None,
        },
    }

    if result is not None:
        salida["result"] = {
            "concepts": {k: cents(v) for k, v in result.concepts.items()},
            "taxes": {k: cents(v) for k, v in result.taxes.items()},
            "subtotal": cents(result.subtotal),
            "total": cents(result.total),
            "forecast": cents(data.get("forecast", 0.0)),
            "imported_kwh": round(result.imported_kwh, 3),
            "exported_kwh": round(result.exported_kwh, 3),
            "energy_by_period": {k: round(v, 3) for k, v in result.energy_by_period.items()},
            "submeter_kwh": {k: round(v, 3) for k, v in result.submeter_kwh.items()},
        }

    # The engine's view of the tariff, with the price series summarised rather
    # than dumped: a month of hourly prices is seven hundred numbers nobody
    # reads, while how many hours were covered is exactly what you want to know.
    salida["prices"] = data.get("prices")
    salida["engine_config"] = data.get("engine_config")
    return salida
