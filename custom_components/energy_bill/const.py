"""Names and defaults for the Energy Bill integration."""

DOMAIN = "energy_bill"

# --- configuration keys -------------------------------------------------
CONF_CONSUMPTION = "consumption_entity"
CONF_EXPORT = "export_entity"
CONF_CYCLE_DAY = "cycle_day"

CONF_POWER_P1_KW = "power_p1_kw"
CONF_POWER_P2_KW = "power_p2_kw"
CONF_POWER_P1_PRICE = "power_p1_price"
CONF_POWER_P2_PRICE = "power_p2_price"

# How the energy is priced, as two independent questions. They really are
# independent: whether the price changes with the hour of the day, and where
# the number comes from. All four answers exist in the wild — including the one
# a single question would have hidden, a tariff with three periods where each
# period has its own entity publishing it.
CONF_ENERGY_FLAT = "energy_flat"
CONF_ENERGY_SOURCE = "energy_source"
SOURCE_FIXED = "fixed"
SOURCE_ENTITY = "entity"
ENERGY_SOURCES = (SOURCE_FIXED, SOURCE_ENTITY)

CONF_ENERGY_PRICE = "energy_price"
CONF_ENERGY_PRICE_P2 = "energy_price_p2"
CONF_ENERGY_PRICE_P3 = "energy_price_p3"
CONF_ENERGY_PRICE_ENTITY = "energy_price_entity"
CONF_ENERGY_PRICE_ENTITY_P2 = "energy_price_entity_p2"
CONF_ENERGY_PRICE_ENTITY_P3 = "energy_price_entity_p3"
CONF_ENERGY_PRICE_TAXED = "energy_price_taxed"
CONF_SURPLUS_PRICE = "surplus_price"
CONF_SURPLUS_PRICE_ENTITY = "surplus_price_entity"
CONF_SURPLUS_PRICE_TAXED = "surplus_price_taxed"
CONF_SURPLUS_CAPPED = "surplus_capped"
CONF_HOURLY_NETTING = "hourly_netting"
# A virtual battery: the balance in euros that surplus turns into once it has
# nothing left to cancel out. Solar Wallet at Octopus, Solar Cloud at
# Iberdrola, Solify at Repsol — same idea, different name.
CONF_BATTERY_ENTITY = "battery_entity"

# Regulated charges: the same for every household in Spain, set by decree.
CONF_BONO_SOCIAL = "bono_social"
CONF_METER_RENTAL = "meter_rental"
# What your particular retailer sells you on top, which is a different kind of
# thing entirely: a name they invented, a price they chose, and often a
# discount they attach to it.
CONF_MONTHLY_FEE = "monthly_fee"
CONF_MONTHLY_FEE_NAME = "monthly_fee_name"
CONF_DISCOUNT_PERCENT = "discount_percent"
CONF_DISCOUNT_NAME = "discount_name"

CONF_TAX_ELECTRICITY = "tax_electricity"
CONF_TAX_VAT = "tax_vat"
# The one real difference found between Octopus and Iberdrola: whether the
# bono social sits inside the electricity-tax base. It is a toggle because it
# changes the number, not the wording.
CONF_TAX_INCLUDES_BONO = "tax_includes_bono"

# --- defaults, from the Spanish 2.0TD tariff ----------------------------
DEFAULT_CYCLE_DAY = 1
DEFAULT_TAX_ELECTRICITY = 5.11269632
DEFAULT_TAX_VAT = 21.0
DEFAULT_SURPLUS_CAPPED = True
DEFAULT_HOURLY_NETTING = True

# Some steps of the wizard are grouped into collapsible sections; these are
# their keys, and the input arrives nested under them.
SECTIONS = ("sources", "power", "regulated", "retailer", "taxes")

# --- concept ids the sensors and taxes refer to -------------------------
ID_POWER = "power"
ID_ENERGY = "energy"
ID_SURPLUS = "surplus"
ID_BONO = "bono_social"
ID_RENTAL = "meter_rental"
ID_FEE = "monthly_fee"
ID_IEE = "iee"
ID_VAT = "vat"
ID_BATTERY = "virtual_battery"
ID_DISCOUNT = "discount"


# --- reading a setup made before a question existed ----------------------
# Both flags were added after the first release, so an entry saved earlier has
# neither. What it does have is the fields themselves, and those say plainly
# enough what was meant: an entity was chosen, or a P2 price was typed.
def stored_flat(data: dict) -> bool:
    """Whether the tariff charges the same at every hour."""
    if CONF_ENERGY_FLAT in data:
        return bool(data[CONF_ENERGY_FLAT])
    return data.get(CONF_ENERGY_PRICE_P2) is None and not data.get(
        CONF_ENERGY_PRICE_ENTITY_P2
    )


def stored_source(data: dict) -> str:
    """Whether the price is a number typed in or an entity publishing it."""
    if data.get(CONF_ENERGY_SOURCE) in ENERGY_SOURCES:
        return str(data[CONF_ENERGY_SOURCE])
    return SOURCE_ENTITY if data.get(CONF_ENERGY_PRICE_ENTITY) else SOURCE_FIXED
