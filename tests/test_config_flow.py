"""The setup wizard, walked end to end with Home Assistant stubbed out.

The form is where the mistakes live. Not arithmetic mistakes — those the engine
test catches — but the kind that only show up in front of a person: a step that
leads nowhere, a field with no label, a price left behind when the tariff
changes shape. None of that needs Home Assistant running; it needs the flow
executed and the translation files read, which is what this does.

The stubs below are deliberately dumb. They record what the flow asked for and
answer what it needs to keep going, nothing more.

Run with: python tests/test_config_flow.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import sys
import types

RAIZ = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "energy_bill"

fallos: list[str] = []


def igual(nombre: str, obtenido, esperado) -> None:
    ok = obtenido == esperado
    print(f"  {'OK ' if ok else 'NO '} {nombre:<52} {obtenido if ok else f'{obtenido!r} != {esperado!r}'}")
    if not ok:
        fallos.append(nombre)


def cierto(nombre: str, condicion, detalle: str = "") -> None:
    print(f"  {'OK ' if condicion else 'NO '} {nombre:<52} {'' if condicion else detalle}")
    if not condicion:
        fallos.append(nombre)


# --- the stubs ----------------------------------------------------------
class _Marker:
    """vol.Required / vol.Optional: a key with a suggested value attached."""

    def __init__(self, schema, description=None, **_):
        self.schema = schema
        self.description = description or {}


class _Schema:
    def __init__(self, schema, **_):
        self.schema = schema


class _Section:
    def __init__(self, schema, options=None):
        self.schema = schema
        self.options = options or {}


def _selector_stub(nombre):
    return type(nombre, (), {"__init__": lambda self, *a, **k: None})


def instalar_stubs() -> None:
    vol = types.ModuleType("voluptuous")
    vol.Schema = _Schema
    vol.Required = type("Required", (_Marker,), {})
    vol.Optional = type("Optional", (_Marker,), {})
    sys.modules["voluptuous"] = vol

    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    sys.modules["homeassistant"] = ha

    entries = types.ModuleType("homeassistant.config_entries")

    class ConfigFlow:
        # The real one takes `domain=` as a class keyword; swallow it.
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

        def async_show_form(self, *, step_id, data_schema, errors=None):
            return {"type": "form", "step_id": step_id, "schema": data_schema, "errors": errors or {}}

        def async_create_entry(self, *, title, data):
            return {"type": "create", "title": title, "data": data}

        def async_update_reload_and_abort(self, entry, *, data, options):
            return {"type": "update", "data": data, "options": options}

    class OptionsFlow(ConfigFlow):
        pass

    entries.ConfigFlow = ConfigFlow
    entries.OptionsFlow = OptionsFlow
    entries.ConfigEntry = type("ConfigEntry", (), {})
    sys.modules["homeassistant.config_entries"] = entries

    core = types.ModuleType("homeassistant.core")
    core.callback = lambda func: func
    sys.modules["homeassistant.core"] = core

    flow = types.ModuleType("homeassistant.data_entry_flow")
    flow.section = _Section
    sys.modules["homeassistant.data_entry_flow"] = flow

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    sel = types.ModuleType("homeassistant.helpers.selector")
    for nombre in (
        "EntitySelector", "EntitySelectorConfig", "NumberSelector", "NumberSelectorConfig",
        "SelectSelector", "SelectSelectorConfig", "TextSelector", "BooleanSelector",
    ):
        setattr(sel, nombre, _selector_stub(nombre))
    sel.NumberSelectorMode = type("NumberSelectorMode", (), {"BOX": "box", "SLIDER": "slider"})
    sel.SelectSelectorMode = type("SelectSelectorMode", (), {"DROPDOWN": "dropdown", "LIST": "list"})
    helpers.selector = sel
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.selector"] = sel


def cargar():
    instalar_stubs()
    paquete = types.ModuleType("eb")
    paquete.__path__ = [str(RAIZ)]
    sys.modules["eb"] = paquete
    modulos = {}
    for nombre in ("const", "config_flow"):
        spec = importlib.util.spec_from_file_location(f"eb.{nombre}", RAIZ / f"{nombre}.py")
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[f"eb.{nombre}"] = modulo
        spec.loader.exec_module(modulo)
        modulos[nombre] = modulo
    return modulos["config_flow"], modulos["const"]


cf, const = cargar()


# --- helpers ------------------------------------------------------------
def campos(resultado) -> dict[str | None, list[str]]:
    """The fields a form shows, grouped by section (None = not in a section)."""
    fuera: dict[str | None, list[str]] = {}
    for marca, valor in resultado["schema"].schema.items():
        if isinstance(valor, _Section):
            fuera[marca.schema] = [m.schema for m in valor.schema.schema]
        else:
            fuera.setdefault(None, []).append(marca.schema)
    return fuera


def respuesta(resultado, valores: dict) -> dict:
    """Fill a form: values go into the section they belong to."""
    fuera: dict = {}
    for marca, valor in resultado["schema"].schema.items():
        if isinstance(valor, _Section):
            fuera[marca.schema] = {
                m.schema: valores[m.schema] for m in valor.schema.schema if m.schema in valores
            }
        elif marca.schema in valores:
            fuera[marca.schema] = valores[marca.schema]
    return fuera


RESPUESTAS = {
    "consumption_entity": "sensor.consumo",
    "export_entity": "sensor.vertido",
    "cycle_day": "1",
    "power_p1_kw": 4.6,
    "power_p1_price": 0.09537,
    "power_p2_kw": 4.6,
    "power_p2_price": 0.02518,
    "energy_price": 0.120331,
    "energy_price_p2": 0.1,
    "energy_price_p3": 0.08,
    "energy_price_entity": "sensor.precio_actual",
    "energy_price_taxed": True,
    "surplus_price": 0.05,
    "battery_entity": "sensor.solar_wallet",
    "surplus_capped": True,
    "bono_social": 0.02484,
    "meter_rental": 0.02677,
    "monthly_fee": 0.0,
    "monthly_fee_name": "",
    "tax_electricity": 5.11269632,
    "tax_vat": 21.0,
    "hourly_netting": True,
}


async def recorrer(modo: str, flujo=None) -> tuple[list[str], dict, list[tuple[str, dict]]]:
    """Walk the whole wizard answering everything, and report what it asked."""
    flujo = flujo or cf.EnergyBillConfigFlow()
    visitados: list[str] = []
    formularios: list[tuple[str, dict]] = []
    resultado = await flujo.async_step_user()
    entrada = None
    while resultado["type"] == "form":
        paso = resultado["step_id"]
        visitados.append(paso)
        formularios.append((paso, campos(resultado)))
        valores = dict(RESPUESTAS)
        if paso == "pricing":
            valores = {const.CONF_ENERGY_PRICE_MODE: modo}
        entrada = respuesta(resultado, valores)
        resultado = await getattr(flujo, f"async_step_{paso}")(entrada)
        if len(visitados) > 10:
            raise AssertionError(f"el asistente no termina: {visitados}")
    return visitados, resultado, formularios


print("════ the wizard asks the right questions, in order ════")
for modo, esperado in (
    (const.MODE_FLAT, ["energy_price"]),
    (const.MODE_PERIODS, ["energy_price", "energy_price_p2", "energy_price_p3"]),
    (const.MODE_ENTITY, ["energy_price_entity", "energy_price_taxed"]),
):
    pasos, final, formularios = asyncio.run(recorrer(modo))
    igual(f"[{modo}] steps", pasos, ["user", "pricing", "energy", "surplus", "extras"])
    paso_energia = dict(formularios)["energy"][None]
    igual(f"[{modo}] the energy step asks for exactly", paso_energia, esperado)
    igual(f"[{modo}] ends by creating the entry", final["type"], "create")

print("\n════ a price that no longer applies is not kept ════")
# Someone sets up per-period prices and later moves to a flat tariff. If P2 and
# P3 survived that, the coordinator would go on billing periods the form no
# longer shows.
_, final_periodos, _ = asyncio.run(recorrer(const.MODE_PERIODS))
cierto("per-period setup keeps P2 and P3", const.CONF_ENERGY_PRICE_P2 in final_periodos["data"])
flujo = cf.EnergyBillConfigFlow()
flujo._current = dict(final_periodos["data"])
_, final_plano, _ = asyncio.run(recorrer(const.MODE_FLAT, flujo))
cierto("switching to flat drops P2", const.CONF_ENERGY_PRICE_P2 not in final_plano["data"])
cierto("switching to flat drops P3", const.CONF_ENERGY_PRICE_P3 not in final_plano["data"])

_, final_entidad, _ = asyncio.run(recorrer(const.MODE_ENTITY))
cierto("the entity setup keeps the entity", const.CONF_ENERGY_PRICE_ENTITY in final_entidad["data"])
cierto("and drops the fixed price", const.CONF_ENERGY_PRICE not in final_entidad["data"])

print("\n════ a bill with no price is refused ════")
flujo = cf.EnergyBillConfigFlow()
asyncio.run(flujo.async_step_user(respuesta(asyncio.run(flujo.async_step_user()), RESPUESTAS)))
asyncio.run(flujo.async_step_pricing({const.CONF_ENERGY_PRICE_MODE: const.MODE_FLAT}))
vacio = asyncio.run(flujo.async_step_energy({}))
igual("empty price stays on the step", vacio["step_id"], "energy")
igual("and says why", vacio["errors"].get("base"), "no_energy_price")

print("\n════ every field a person sees has words next to it ════")
# A field with no label renders as its raw key. It has happened, and the only
# way to notice is to check every field of every step against the files.
_, _, formularios = asyncio.run(recorrer(const.MODE_PERIODS))
_, _, formularios_entidad = asyncio.run(recorrer(const.MODE_ENTITY))
todos = {paso: dict(campos) for paso, campos in formularios}
for paso, campos_paso in formularios_entidad:
    for seccion, claves in campos_paso.items():
        todos.setdefault(paso, {}).setdefault(seccion, [])
        todos[paso][seccion] = sorted(set(todos[paso][seccion]) | set(claves))

for idioma in ("en", "es"):
    datos = json.loads((RAIZ / "translations" / f"{idioma}.json").read_text(encoding="utf-8"))
    faltan: list[str] = []
    for paso, secciones in todos.items():
        for ambito in ("config", "options"):
            bloque = datos[ambito]["step"].get(paso)
            if bloque is None:
                faltan.append(f"{ambito}.step.{paso}")
                continue
            for seccion, claves in secciones.items():
                sub = bloque if seccion is None else bloque.get("sections", {}).get(seccion, {})
                if seccion is not None and not sub:
                    faltan.append(f"{ambito}.step.{paso}.sections.{seccion}")
                    continue
                for clave in claves:
                    if clave not in sub.get("data", {}):
                        faltan.append(f"{ambito}.step.{paso}[{seccion}].data.{clave}")
                    # The explanation under the field is only required where a
                    # person meets it for the first time, which is setup.
                    if ambito == "config" and clave not in sub.get("data_description", {}):
                        faltan.append(f"{ambito}.step.{paso}[{seccion}].data_description.{clave}")
    cierto(f"[{idioma}] every field is named and explained", not faltan, str(faltan[:4]))

    modos = datos["selector"]["energy_price_mode"]["options"]
    cierto(f"[{idioma}] the three tariff shapes are named", set(modos) == set(const.ENERGY_PRICE_MODES))
    for error in ("no_energy_price", "no_price_entity"):
        cierto(f"[{idioma}] error «{error}» has a message", error in datos["config"]["error"])
    cierto(f"[{idioma}] the virtual battery has a name", const.ID_BATTERY in datos["common"])

print("\n" + ("  ALL GOOD" if not fallos else f"  {len(fallos)} FAILED: {fallos}"))
sys.exit(1 if fallos else 0)
