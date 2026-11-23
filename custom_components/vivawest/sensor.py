"""Sensoren: Verbrauch des letzten Monats und aktuelle Mietkonditionen."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import CURRENCY_EURO, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import VivawestData
from .const import DOMAIN
from .coordinator import VivawestConfigEntry, VivawestCoordinator


# ---------------------------------------------------------------------------
# Mietkonditionen
# ---------------------------------------------------------------------------
def _amount(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _items(period: dict[str, Any]) -> list[dict[str, Any]]:
    return (period.get("ConditionItems") or {}).get("results") or []


def period_amounts(period: dict[str, Any]) -> dict[str, float]:
    """{Konditionsart: Betrag} eines Zeitraums, plus "gesamt"."""
    amounts = {"gesamt": _amount(period.get("GrossAmountdec"))}
    for item in _items(period):
        if item.get("Condtype"):
            amounts[item["Condtype"]] = _amount(item.get("GrossAmountdec"))
    return amounts


def select_periods(
    periods: list[dict[str, Any]], today: date
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Aktuell gültiger Zeitraum (Flag ``Gueltig``, sonst per Datum) und zukünftige Zeiträume."""
    ordered = sorted(periods, key=lambda p: p.get("GueltigVon") or "")
    today_s = today.isoformat()
    current = next((p for p in ordered if p.get("Gueltig")), None)
    if current is None:
        current = next(
            (p for p in reversed(ordered)
             if (p.get("GueltigVon") or "") <= today_s <= (p.get("GueltigBis") or "9999")),
            None,
        )
    if current is None:
        return None, []
    start = current.get("GueltigVon") or ""
    return current, [p for p in ordered if (p.get("GueltigVon") or "") > start]


def condition_value(data: VivawestData, condtype: str) -> float | None:
    current, _ = select_periods(data.conditions, dt_util.now().date())
    if current is None:
        return None
    return period_amounts(current).get(condtype)


def condition_attributes(data: VivawestData, condtype: str) -> dict[str, Any]:
    current, future = select_periods(data.conditions, dt_util.now().date())
    if current is None:
        return {}
    now = period_amounts(current).get(condtype)
    attrs: dict[str, Any] = {
        "gueltig_von": current.get("GueltigVon"),
        "gueltig_bis": current.get("GueltigBis"),
    }
    if condtype == "gesamt":
        attrs["positionen"] = {
            item.get("Xcondtypel") or item.get("Condtype"): _amount(item.get("GrossAmountdec"))
            for item in _items(current)
        }
    for period in future:  # erste geplante Änderung (fehlende Position = 0 €)
        amount = period_amounts(period).get(condtype, 0.0)
        if amount != now:
            attrs["naechster_betrag"] = amount
            attrs["naechste_aenderung"] = period.get("GueltigVon")
            break
    return attrs


# ---------------------------------------------------------------------------
# Verbräuche
# ---------------------------------------------------------------------------
def consumption_value(data: VivawestData, uvi_type: str, field: str) -> float | None:
    value = (data.current.get(uvi_type) or {}).get(field)
    return float(value) if value is not None else None


def consumption_attributes(data: VivawestData, uvi_type: str) -> dict[str, Any]:
    entry = data.current.get(uvi_type) or {}
    attrs = {k: entry.get(k) for k in ("ablesedatum", "monat", "jahr")}
    diff = entry.get("differenz_liegenschaft_qm_prozent")
    if isinstance(diff, (int, float)):
        attrs["vergleich_liegenschaft_prozent"] = round(diff, 1)
    return attrs


# ---------------------------------------------------------------------------
# Beschreibungen
# ---------------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class VivawestSensorDescription(SensorEntityDescription):
    value_fn: Callable[[VivawestData], float | None]
    attrs_fn: Callable[[VivawestData], dict[str, Any]]


def _consumption(key: str, uvi_type: str, field: str, unit: str,
                 device_class: SensorDeviceClass | None) -> VivawestSensorDescription:
    return VivawestSensorDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=unit,
        device_class=device_class,
        # Bewusst ohne state_class: Monatsverbrauch, kein Zählerstand.
        # Die Langzeitstatistik liefert der Statistik-Import.
        value_fn=lambda d: consumption_value(d, uvi_type, field),
        attrs_fn=lambda d: consumption_attributes(d, uvi_type),
    )


def _cost(key: str, condtype: str) -> VivawestSensorDescription:
    return VivawestSensorDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        value_fn=lambda d: condition_value(d, condtype),
        attrs_fn=lambda d: condition_attributes(d, condtype),
    )


SENSORS: tuple[VivawestSensorDescription, ...] = (
    _consumption("kaltwasser", "kaltwasser", "verbrauchswert",
                 UnitOfVolume.CUBIC_METERS, SensorDeviceClass.WATER),
    _consumption("warmwasser", "warmwasser", "verbrauchswert",
                 UnitOfVolume.CUBIC_METERS, SensorDeviceClass.WATER),
    _consumption("warmwasser_energie", "warmwasser", "kwh",
                 UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY),
    _consumption("heizenergie", "heizenergie", "kwh",
                 UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY),
    _consumption("heizung_verbrauchseinheiten", "heizenergie", "verbrauchswert", "VE", None),
    # Konditionsarten des Portals: 1000 Grundmiete, 4000 VZ Betriebskosten, 4010 VZ Heizkosten
    _cost("miete_gesamt", "gesamt"),
    _cost("grundmiete", "1000"),
    _cost("vorauszahlung_betriebskosten", "4000"),
    _cost("vorauszahlung_heizkosten", "4010"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VivawestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(VivawestSensor(coordinator, description) for description in SENSORS)


class VivawestSensor(CoordinatorEntity[VivawestCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: VivawestSensorDescription

    def __init__(self, coordinator: VivawestCoordinator,
                 description: VivawestSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name="Vivawest",
            manufacturer="Vivawest",
            model="Kundenportal",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://kundenportal.vivawest.de",
        )

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self.coordinator.data)
