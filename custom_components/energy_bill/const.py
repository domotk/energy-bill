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

CONF_ENERGY_PRICE = "energy_price"
CONF_ENERGY_PRICE_P2 = "energy_price_p2"
CONF_ENERGY_PRICE_P3 = "energy_price_p3"
CONF_ENERGY_PRICE_ENTITY = "energy_price_entity"
CONF_ENERGY_PRICE_TAXED = "energy_price_taxed"
CONF_SURPLUS_PRICE = "surplus_price"
CONF_SURPLUS_PRICE_ENTITY = "surplus_price_entity"
CONF_SURPLUS_PRICE_TAXED = "surplus_price_taxed"
CONF_SURPLUS_CAPPED = "surplus_capped"
CONF_HOURLY_NETTING = "hourly_netting"

CONF_BONO_SOCIAL = "bono_social"
CONF_METER_RENTAL = "meter_rental"
CONF_MONTHLY_FEE = "monthly_fee"
CONF_MONTHLY_FEE_NAME = "monthly_fee_name"

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

# The form is grouped into collapsible sections; these are their keys, and the
# input arrives nested under them.
SECTIONS = ("sources", "power", "energy", "surplus", "fixed", "taxes", "advanced")

# --- concept ids the sensors and taxes refer to -------------------------
ID_POWER = "power"
ID_ENERGY = "energy"
ID_SURPLUS = "surplus"
ID_BONO = "bono_social"
ID_RENTAL = "meter_rental"
ID_FEE = "monthly_fee"
ID_IEE = "iee"
ID_VAT = "vat"
