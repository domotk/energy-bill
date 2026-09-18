"""Setting up a bill, and editing it afterwards.

The form is grouped into collapsible sections, with everything a normal Spanish
household needs open and the rest folded away. Whoever fills this in has a
contract in one hand and is not necessarily sure what any of it means, so every
field carries a line of explanation and the numeric ones are bounded to values
that can actually exist.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_BONO_SOCIAL,
    CONF_CONSUMPTION,
    CONF_CYCLE_DAY,
    CONF_ENERGY_PRICE,
    CONF_ENERGY_PRICE_ENTITY,
    CONF_ENERGY_PRICE_P2,
    CONF_ENERGY_PRICE_P3,
    CONF_ENERGY_PRICE_TAXED,
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
    SECTIONS,
)

_ENERGY_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="energy")
)
_PRICE_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="monetary")
)
_TEXT = selector.TextSelector()
_BOOL = selector.BooleanSelector()


def _number(step: float | str = "any"):
    """A number box that does not quantise what you type.

    `step` has to be at least 0.001 or the literal "any"; anything smaller is
    rejected, and the rejection arrives as a bare HTTP 400 with nothing in the
    log. Prices need more precision than a thousandth — 0,09537, not 0,095.
    """
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, step=step, mode=selector.NumberSelectorMode.BOX
        )
    )


def _kilowatts():
    """Contracted power as a slider over what a home can actually contract.

    Nothing is supplied below 1 kW, and above 15 kW you are off the domestic
    2.0TD access tariff altogether. Bounding it also catches the commonest
    slip: typing watts where kilowatts were asked for.
    """
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1,
            max=15,
            step=0.1,
            unit_of_measurement="kW",
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )


def _day_of_month():
    """The day the cycle starts, as a list rather than a free-text number.

    Stops at 28 so the cycle also exists in February.
    """
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[str(d) for d in range(1, 29)],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _percent():
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=100,
            step="any",
            unit_of_measurement="%",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def schema(current: dict[str, Any] | None = None) -> vol.Schema:
    """Build the grouped form, pre-filled with whatever is already configured.

    Pre-filling goes through `description={"suggested_value": ...}` and never
    through `default=`: a default belongs to the schema and would be sent even
    when there is nothing to send.
    """
    d = current or {}

    def pre(key, fallback=None):
        value = d.get(key, fallback)
        return {"description": {"suggested_value": value}} if value is not None else {}

    sources = vol.Schema(
        {
            vol.Required(CONF_CONSUMPTION, **pre(CONF_CONSUMPTION)): _ENERGY_SENSOR,
            vol.Optional(CONF_EXPORT, **pre(CONF_EXPORT)): _ENERGY_SENSOR,
            vol.Required(
                CONF_CYCLE_DAY, **pre(CONF_CYCLE_DAY, str(DEFAULT_CYCLE_DAY))
            ): _day_of_month(),
        }
    )
    power = vol.Schema(
        {
            vol.Required(CONF_POWER_P1_KW, **pre(CONF_POWER_P1_KW, 4.6)): _kilowatts(),
            vol.Required(CONF_POWER_P1_PRICE, **pre(CONF_POWER_P1_PRICE)): _number(),
            vol.Required(CONF_POWER_P2_KW, **pre(CONF_POWER_P2_KW, 4.6)): _kilowatts(),
            vol.Required(CONF_POWER_P2_PRICE, **pre(CONF_POWER_P2_PRICE)): _number(),
        }
    )
    # One section per price, each holding its three related fields together: the
    # fixed value, the entity that may replace it, and whether that entity
    # already carries taxes. Energy and surplus are separate sections because
    # the two can disagree — Octopus publishes the purchase price with taxes and
    # the surplus price without — and one flag for both would be wrong for one.
    energy = vol.Schema(
        {
            vol.Optional(CONF_ENERGY_PRICE, **pre(CONF_ENERGY_PRICE)): _number(),
            vol.Optional(
                CONF_ENERGY_PRICE_ENTITY, **pre(CONF_ENERGY_PRICE_ENTITY)
            ): _PRICE_SENSOR,
            vol.Optional(
                CONF_ENERGY_PRICE_TAXED, **pre(CONF_ENERGY_PRICE_TAXED, False)
            ): _BOOL,
        }
    )
    surplus = vol.Schema(
        {
            vol.Optional(CONF_SURPLUS_PRICE, **pre(CONF_SURPLUS_PRICE, 0.0)): _number(),
            vol.Optional(
                CONF_SURPLUS_PRICE_ENTITY, **pre(CONF_SURPLUS_PRICE_ENTITY)
            ): _PRICE_SENSOR,
            vol.Optional(
                CONF_SURPLUS_PRICE_TAXED, **pre(CONF_SURPLUS_PRICE_TAXED, False)
            ): _BOOL,
        }
    )
    fixed = vol.Schema(
        {
            vol.Optional(CONF_BONO_SOCIAL, **pre(CONF_BONO_SOCIAL, 0.0)): _number(),
            vol.Optional(CONF_METER_RENTAL, **pre(CONF_METER_RENTAL, 0.0)): _number(),
            vol.Optional(CONF_MONTHLY_FEE, **pre(CONF_MONTHLY_FEE, 0.0)): _number(),
            vol.Optional(CONF_MONTHLY_FEE_NAME, **pre(CONF_MONTHLY_FEE_NAME, "")): _TEXT,
        }
    )
    taxes = vol.Schema(
        {
            vol.Required(
                CONF_TAX_ELECTRICITY,
                **pre(CONF_TAX_ELECTRICITY, DEFAULT_TAX_ELECTRICITY),
            ): _percent(),
            vol.Required(CONF_TAX_VAT, **pre(CONF_TAX_VAT, DEFAULT_TAX_VAT)): _percent(),
            vol.Optional(
                CONF_TAX_INCLUDES_BONO, **pre(CONF_TAX_INCLUDES_BONO, False)
            ): _BOOL,
        }
    )
    advanced = vol.Schema(
        {
            vol.Optional(CONF_ENERGY_PRICE_P2, **pre(CONF_ENERGY_PRICE_P2)): _number(),
            vol.Optional(CONF_ENERGY_PRICE_P3, **pre(CONF_ENERGY_PRICE_P3)): _number(),
            vol.Optional(
                CONF_SURPLUS_CAPPED, **pre(CONF_SURPLUS_CAPPED, DEFAULT_SURPLUS_CAPPED)
            ): _BOOL,
            vol.Optional(
                CONF_HOURLY_NETTING, **pre(CONF_HOURLY_NETTING, DEFAULT_HOURLY_NETTING)
            ): _BOOL,
        }
    )

    return vol.Schema(
        {
            vol.Required("sources"): section(sources, {"collapsed": False}),
            vol.Required("power"): section(power, {"collapsed": False}),
            vol.Required("energy"): section(energy, {"collapsed": False}),
            vol.Required("surplus"): section(surplus, {"collapsed": False}),
            vol.Required("fixed"): section(fixed, {"collapsed": True}),
            vol.Required("taxes"): section(taxes, {"collapsed": True}),
            vol.Required("advanced"): section(advanced, {"collapsed": True}),
        }
    )


def flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """Sections arrive nested; everything downstream wants one flat dict."""
    flat: dict[str, Any] = {}
    for key, value in user_input.items():
        if key in SECTIONS and isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def _validate(data: dict[str, Any]) -> dict[str, str]:
    """A bill with no energy price is not a bill, so refuse it here."""
    if not data.get(CONF_ENERGY_PRICE) and not data.get(CONF_ENERGY_PRICE_ENTITY):
        return {"base": "no_energy_price"}
    return {}


class EnergyBillConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            data = flatten(user_input)
            errors = _validate(data)
            if not errors:
                return self.async_create_entry(title="Factura de luz", data=data)
        return self.async_show_form(step_id="user", data_schema=schema(), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Change the setup of an existing bill, sensors included.

        The options flow can already edit every price, but not the entities the
        bill is built from: those live in the entry's data. Without this step,
        swapping a meter means deleting the entry and losing its history.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = flatten(user_input)
            errors = _validate(data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data, options={})
        current = {**entry.data, **entry.options}
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema(current), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return EnergyBillOptionsFlow()


class EnergyBillOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            data = flatten(user_input)
            errors = _validate(data)
            if not errors:
                return self.async_create_entry(title="", data=data)
        return self.async_show_form(
            step_id="init", data_schema=schema(current), errors=errors
        )
