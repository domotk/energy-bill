"""What the bill looks like from outside: a handful of sensors.

The breakdown rides as attributes on the cost sensor rather than as one entity
per concept. A concept list is open-ended — a retailer can invent a new fee
tomorrow — and one entity per line would mean the entity registry changing
shape every time somebody edits their tariff.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BillCoordinator
from .engine import cents

# `common` rather than a category named after what it holds: hassfest checks
# strings.json against a closed set of top-level keys, and a `concepts` one —
# which loads perfectly well at runtime — fails validation. `common` is the
# only category whose shape is a free list of slug → text, which is precisely
# what a concept name is.
_CATEGORY = "common"
_PREFIX = f"component.{DOMAIN}.{_CATEGORY}."


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BillCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Concept names come from the translation files, in the language Home
    # Assistant is set to. Hard-coding them would have shipped a Spanish
    # breakdown to everyone, which is the sort of thing nobody notices until
    # somebody outside Spain installs it.
    resources = await async_get_translations(
        hass, hass.config.language, _CATEGORY, {DOMAIN}
    )
    names = {k[len(_PREFIX):]: v for k, v in resources.items() if k.startswith(_PREFIX)}
    async_add_entities(
        [
            CycleCostSensor(coordinator, entry, names),
            ForecastSensor(coordinator, entry),
            ImportedSensor(coordinator, entry),
            ExportedSensor(coordinator, entry),
        ]
    )


class BillEntity(CoordinatorEntity[BillCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: BillCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Energy Bill",
            entry_type="service",
        )


class CycleCostSensor(BillEntity):
    """What the cycle has cost so far."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    # TOTAL rather than TOTAL_INCREASING: the figure drops back to nearly zero
    # when the cycle rolls over, and that is a reset, not a meter rollback.
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:receipt-text"

    def __init__(self, coordinator, entry, names: dict[str, str]):
        super().__init__(coordinator, entry, "cycle_cost")
        self._names = names

    def _label(self, concept_id: str, own: dict[str, str]) -> str:
        """A name the user typed wins; then the translation; then the raw id."""
        return own.get(concept_id) or self._names.get(concept_id, concept_id)

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return cents(data["result"].total) if data else None

    @property
    def last_reset(self):
        return self.coordinator.data["cycle_start"] if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if not data:
            return {}
        result = data["result"]
        return {
            "concepts": {
                self._label(k, result.names): cents(v) for k, v in result.concepts.items()
            },
            "taxes": {self._label(k, {}): cents(v) for k, v in result.taxes.items()},
            "subtotal": cents(result.subtotal),
            "energy_by_period": {k: round(v, 2) for k, v in result.energy_by_period.items()},
            "cycle_start": data["cycle_start"].isoformat(),
            "cycle_end": data["cycle_end"].isoformat(),
            "elapsed_days": round(data["elapsed_days"], 2),
            "cycle_days": data["cycle_days"],
        }


class ForecastSensor(BillEntity):
    """What the cycle will cost if the rest of it looks like what has happened."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_icon = "mdi:receipt-text-clock"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "forecast")

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return cents(data["forecast"]) if data else None


class ImportedSensor(BillEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "imported")

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return round(data["result"].imported_kwh, 2) if data else None

    @property
    def last_reset(self):
        return self.coordinator.data["cycle_start"] if self.coordinator.data else None


class ExportedSensor(BillEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "exported")

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return round(data["result"].exported_kwh, 2) if data else None

    @property
    def last_reset(self):
        return self.coordinator.data["cycle_start"] if self.coordinator.data else None
