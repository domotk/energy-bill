"""Setting up a bill, and editing it afterwards.

A wizard rather than one long form. Whoever fills this in has a contract in one
hand and is not necessarily sure what any of it means, so each step asks about
one thing, every field carries a line of explanation, and the numeric ones are
bounded to values that can actually exist.

The energy price is the part that used to be spread out — a price here, the
per-period prices folded away under "advanced" over there. Now one question
decides the shape ("the same at every hour", "one price per period", "an entity
publishes it") and the next step asks for exactly the fields that answer
implies, and nothing else.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_ENTITY,
    CONF_BONO_SOCIAL,
    CONF_CONSUMPTION,
    CONF_CYCLE_DAY,
    CONF_ENERGY_PRICE,
    CONF_ENERGY_PRICE_ENTITY,
    CONF_ENERGY_PRICE_MODE,
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
    ENERGY_PRICE_MODES,
    MODE_ENTITY,
    MODE_FLAT,
    MODE_PERIODS,
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


def _price_mode():
    """The one question the whole energy step hangs on, as radio buttons."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(ENERGY_PRICE_MODES),
            mode=selector.SelectSelectorMode.LIST,
            translation_key="energy_price_mode",
        )
    )


def infer_mode(current: dict[str, Any]) -> str:
    """Work out the pricing shape of a setup made before the question existed."""
    if current.get(CONF_ENERGY_PRICE_MODE) in ENERGY_PRICE_MODES:
        return str(current[CONF_ENERGY_PRICE_MODE])
    if current.get(CONF_ENERGY_PRICE_ENTITY):
        return MODE_ENTITY
    if current.get(CONF_ENERGY_PRICE_P2) is not None:
        return MODE_PERIODS
    return MODE_FLAT


def flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """Sections arrive nested; everything downstream wants one flat dict."""
    flat: dict[str, Any] = {}
    for key, value in user_input.items():
        if key in SECTIONS and isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def clean(data: dict[str, Any]) -> dict[str, Any]:
    """Drop the price fields the chosen mode does not use.

    Otherwise switching from per-period prices back to a flat one leaves P2 and
    P3 lying around, and the coordinator — which decides by what is present —
    would go on billing the periods nobody can see on the form any more.
    """
    mode = infer_mode(data)
    out = dict(data)
    if mode != MODE_PERIODS:
        out.pop(CONF_ENERGY_PRICE_P2, None)
        out.pop(CONF_ENERGY_PRICE_P3, None)
    if mode == MODE_ENTITY:
        out.pop(CONF_ENERGY_PRICE, None)
    else:
        out.pop(CONF_ENERGY_PRICE_ENTITY, None)
        out.pop(CONF_ENERGY_PRICE_TAXED, None)
    return out


class _Wizard:
    """The steps themselves, shared by setup, reconfiguration and options.

    The three flows ask exactly the same questions; only what happens at the
    end differs, which is `_finish`.
    """

    _current: dict[str, Any]
    _data: dict[str, Any]

    def _pre(self, key: str, fallback: Any = None) -> dict:
        """Pre-fill from what is already configured.

        Through `description={"suggested_value": ...}` and never through
        `default=`: a default belongs to the schema and would be sent even when
        there is nothing to send.
        """
        value = {**self._current, **self._data}.get(key, fallback)
        return {"description": {"suggested_value": value}} if value is not None else {}

    async def _show(self, step_id: str, schema: vol.Schema, errors: dict | None = None):
        return self.async_show_form(
            step_id=step_id, data_schema=schema, errors=errors or {}
        )

    # --- step 1: what is being measured, and how much power -----------------
    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._data.update(flatten(user_input))
            return await self.async_step_pricing()

        sources = vol.Schema(
            {
                vol.Required(CONF_CONSUMPTION, **self._pre(CONF_CONSUMPTION)): _ENERGY_SENSOR,
                vol.Optional(CONF_EXPORT, **self._pre(CONF_EXPORT)): _ENERGY_SENSOR,
                vol.Required(
                    CONF_CYCLE_DAY, **self._pre(CONF_CYCLE_DAY, str(DEFAULT_CYCLE_DAY))
                ): _day_of_month(),
            }
        )
        power = vol.Schema(
            {
                vol.Required(CONF_POWER_P1_KW, **self._pre(CONF_POWER_P1_KW, 4.6)): _kilowatts(),
                vol.Required(CONF_POWER_P1_PRICE, **self._pre(CONF_POWER_P1_PRICE)): _number(),
                vol.Required(CONF_POWER_P2_KW, **self._pre(CONF_POWER_P2_KW, 4.6)): _kilowatts(),
                vol.Required(CONF_POWER_P2_PRICE, **self._pre(CONF_POWER_P2_PRICE)): _number(),
            }
        )
        return await self._show(
            "user",
            vol.Schema(
                {
                    vol.Required("sources"): section(sources, {"collapsed": False}),
                    vol.Required("power"): section(power, {"collapsed": False}),
                }
            ),
        )

    # --- step 2: the one question that shapes the next step -----------------
    async def async_step_pricing(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_energy()

        mode = infer_mode({**self._current, **self._data})
        return await self._show(
            "pricing",
            vol.Schema(
                {
                    vol.Required(
                        CONF_ENERGY_PRICE_MODE,
                        description={"suggested_value": mode},
                    ): _price_mode()
                }
            ),
        )

    # --- step 3: the prices that answer implies, and only those -------------
    async def async_step_energy(self, user_input: dict[str, Any] | None = None):
        mode = infer_mode({**self._current, **self._data})
        errors: dict[str, str] = {}

        if user_input is not None:
            candidate = {**self._data, **user_input}
            if mode == MODE_ENTITY and not candidate.get(CONF_ENERGY_PRICE_ENTITY):
                errors["base"] = "no_price_entity"
            elif mode != MODE_ENTITY and not candidate.get(CONF_ENERGY_PRICE):
                errors["base"] = "no_energy_price"
            if not errors:
                self._data = candidate
                return await self.async_step_surplus()

        if mode == MODE_ENTITY:
            fields = {
                vol.Required(
                    CONF_ENERGY_PRICE_ENTITY, **self._pre(CONF_ENERGY_PRICE_ENTITY)
                ): _PRICE_SENSOR,
                vol.Optional(
                    CONF_ENERGY_PRICE_TAXED, **self._pre(CONF_ENERGY_PRICE_TAXED, False)
                ): _BOOL,
            }
        elif mode == MODE_PERIODS:
            # All three periods together, which is the only way to fill them in:
            # they come off one table on the contract, and a price you cannot
            # see next to its neighbours is a price you cannot check.
            fields = {
                vol.Required(CONF_ENERGY_PRICE, **self._pre(CONF_ENERGY_PRICE)): _number(),
                vol.Required(CONF_ENERGY_PRICE_P2, **self._pre(CONF_ENERGY_PRICE_P2)): _number(),
                vol.Required(CONF_ENERGY_PRICE_P3, **self._pre(CONF_ENERGY_PRICE_P3)): _number(),
            }
        else:
            fields = {
                vol.Required(CONF_ENERGY_PRICE, **self._pre(CONF_ENERGY_PRICE)): _number(),
            }

        # One step id for the three shapes, not `energy_{mode}`: Home Assistant
        # routes the next submission to `async_step_<step_id>`, so a computed id
        # would look for a step that does not exist. The fields differ; the
        # question does not.
        return await self._show("energy", vol.Schema(fields), errors)

    # --- step 4: what comes back the other way ------------------------------
    async def async_step_surplus(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_extras()

        return await self._show(
            "surplus",
            vol.Schema(
                {
                    vol.Optional(
                        CONF_SURPLUS_PRICE, **self._pre(CONF_SURPLUS_PRICE, 0.0)
                    ): _number(),
                    vol.Optional(
                        CONF_SURPLUS_PRICE_ENTITY, **self._pre(CONF_SURPLUS_PRICE_ENTITY)
                    ): _PRICE_SENSOR,
                    vol.Optional(
                        CONF_SURPLUS_PRICE_TAXED, **self._pre(CONF_SURPLUS_PRICE_TAXED, False)
                    ): _BOOL,
                    vol.Optional(
                        CONF_BATTERY_ENTITY, **self._pre(CONF_BATTERY_ENTITY)
                    ): _PRICE_SENSOR,
                    vol.Optional(
                        CONF_SURPLUS_CAPPED, **self._pre(CONF_SURPLUS_CAPPED, DEFAULT_SURPLUS_CAPPED)
                    ): _BOOL,
                }
            ),
        )

    # --- step 5: everything that is the same for nearly everybody -----------
    async def async_step_extras(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._data.update(flatten(user_input))
            return await self._finish(clean({**self._current, **self._data}))

        fixed = vol.Schema(
            {
                vol.Optional(CONF_BONO_SOCIAL, **self._pre(CONF_BONO_SOCIAL, 0.0)): _number(),
                vol.Optional(CONF_METER_RENTAL, **self._pre(CONF_METER_RENTAL, 0.0)): _number(),
                vol.Optional(CONF_MONTHLY_FEE, **self._pre(CONF_MONTHLY_FEE, 0.0)): _number(),
                vol.Optional(CONF_MONTHLY_FEE_NAME, **self._pre(CONF_MONTHLY_FEE_NAME, "")): _TEXT,
            }
        )
        taxes = vol.Schema(
            {
                vol.Required(
                    CONF_TAX_ELECTRICITY,
                    **self._pre(CONF_TAX_ELECTRICITY, DEFAULT_TAX_ELECTRICITY),
                ): _percent(),
                vol.Required(
                    CONF_TAX_VAT, **self._pre(CONF_TAX_VAT, DEFAULT_TAX_VAT)
                ): _percent(),
                vol.Optional(
                    CONF_TAX_INCLUDES_BONO, **self._pre(CONF_TAX_INCLUDES_BONO, False)
                ): _BOOL,
            }
        )
        advanced = vol.Schema(
            {
                vol.Optional(
                    CONF_HOURLY_NETTING, **self._pre(CONF_HOURLY_NETTING, DEFAULT_HOURLY_NETTING)
                ): _BOOL,
            }
        )
        return await self._show(
            "extras",
            vol.Schema(
                {
                    vol.Required("fixed"): section(fixed, {"collapsed": False}),
                    vol.Required("taxes"): section(taxes, {"collapsed": True}),
                    vol.Required("advanced"): section(advanced, {"collapsed": True}),
                }
            ),
        )

    async def _finish(self, data: dict[str, Any]):
        raise NotImplementedError


class EnergyBillConfigFlow(_Wizard, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._current = {}
        self._data = {}
        self._entry: ConfigEntry | None = None

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Change the setup of an existing bill, sensors included.

        The options flow can already edit every price, but not the entities the
        bill is built from: those live in the entry's data. Without this step,
        swapping a meter means deleting the entry and losing its history.

        It runs the same wizard, pre-filled, so there is only one form to keep
        right.
        """
        self._entry = self._get_reconfigure_entry()
        self._current = {**self._entry.data, **self._entry.options}
        self._data = {}
        return await self.async_step_user(user_input)

    async def _finish(self, data: dict[str, Any]):
        if self._entry is not None:
            return self.async_update_reload_and_abort(self._entry, data=data, options={})
        return self.async_create_entry(title="Factura de luz", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return EnergyBillOptionsFlow()


class EnergyBillOptionsFlow(_Wizard, OptionsFlow):
    def __init__(self) -> None:
        self._current = {}
        self._data = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        self._current = {**self.config_entry.data, **self.config_entry.options}
        self._data = {}
        return await self.async_step_user(user_input)

    async def _finish(self, data: dict[str, Any]):
        return self.async_create_entry(title="", data=data)
