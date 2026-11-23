"""Sensoren: Verbräuche, Zählerstände, Mietkonditionen, Abrechnungen, Postfach, Schäden."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util, slugify

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
# Einzelzähler
# ---------------------------------------------------------------------------
METER_UNITS = {"CBM": (UnitOfVolume.CUBIC_METERS, SensorDeviceClass.WATER)}


def find_meter(data: VivawestData, meter_id: int) -> dict[str, Any] | None:
    for meters in data.meters.values():
        for meter in meters:
            if meter["geraet"].get("id") == meter_id:
                return meter
    return None


def latest_reading(meter: dict[str, Any] | None) -> dict[str, Any] | None:
    """Jüngster Monatswert mit Zählerstand (ältere/fehlerhafte Monate haben keinen)."""
    if not meter:
        return None
    valid = [m for m in meter.get("messwerte") or []
             if m.get("verbrauchswert") is not None and m.get("jahr") and m.get("monat")]
    return max(valid, key=lambda m: (int(m["jahr"]), int(m["monat"])), default=None)


def meter_value(data: VivawestData, meter_id: int, field: str) -> float | None:
    reading = latest_reading(find_meter(data, meter_id))
    if reading is None or reading.get(field) is None:
        return None
    return float(reading[field])


def meter_attributes(data: VivawestData, meter_id: int, delta_field: str) -> dict[str, Any]:
    meter = find_meter(data, meter_id)
    if meter is None:
        return {}
    geraet = meter["geraet"]
    attrs: dict[str, Any] = {
        "raum": geraet.get("raum"),
        "zaehlernummer": geraet.get("nr"),
        "geraetetyp": geraet.get("typ"),
    }
    if geraet.get("umrech_faktor") not in (None, "", "1"):
        attrs["umrechnungsfaktor"] = _float(geraet.get("umrech_faktor"))
    reading = latest_reading(meter)
    if reading:
        attrs.update({k: reading.get(k) for k in ("ablesedatum", "monat", "jahr")})
        if reading.get(delta_field) is not None:
            attrs["monatsverbrauch"] = _float(reading[delta_field])
    return attrs


def _float(value: Any) -> float | None:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Nebenkostenabrechnungen
# ---------------------------------------------------------------------------
def _valid_date(value: Any) -> str | None:
    """Datumsteil von ISO-Werten; Platzhalter wie "-001-11-30" ergeben None."""
    if isinstance(value, str) and re.match(r"^(19|20)\d\d-\d\d-\d\d", value):
        return value[:10]
    return None


def settlement_value(data: VivawestData, kind: str) -> float | None:
    settlement = data.settlements.get(kind)
    if settlement is None or settlement.get("AeErgebnis") is None:
        return None
    return _amount(settlement["AeErgebnis"])


def settlement_attributes(data: VivawestData, kind: str) -> dict[str, Any]:
    st = data.settlements.get(kind)
    if st is None:
        return {}
    result = _amount(st.get("AeErgebnis"))
    attrs: dict[str, Any] = {
        # Vorzeichen wie im Portal: > 0 Nachzahlung, sonst Guthaben
        "ergebnis": "Nachzahlung" if result > 0 else "Guthaben",
        "zeitraum_von": _valid_date(st.get("AbrechnungVon")),
        "zeitraum_bis": _valid_date(st.get("AbrechnungBis")),
        "kosten": _amount(st.get("KostenMv")),
        "kosten_vorjahr": _amount(st.get("KostenMvVorjahr")),
        "geleistete_vorauszahlungen": _amount(st.get("Prepayments")),
        "prognose": _amount(st.get("KostenMvPrognose")),
        "prognose_zeitraum": st.get("LabelPrognose") or None,
    }
    if _amount(st.get("NewUnitprice")):
        attrs["neue_vorauszahlung"] = _amount(st.get("NewUnitprice"))
    if faellig := _valid_date(st.get("FaelligZum")):
        attrs["faellig_am"] = faellig
        attrs["betrag_faellig"] = _amount(st.get("BetragFaelligkeit"))
    positions = (st.get("Nebenkostenschluessel") or {}).get("results") or []
    if positions:
        attrs["positionen"] = {
            p.get("NkslText") or p.get("Nksl"): _amount(p.get("KostenNkslMv")) for p in positions
        }
    return attrs


# ---------------------------------------------------------------------------
# Schadensmeldungen und Postfach
# ---------------------------------------------------------------------------
def damage_appointment(damage: dict[str, Any]) -> datetime | None:
    """Handwerkertermin; Uhrzeit aus dem Zeitfenster ("07:00 Uhr"), sonst 00:00."""
    day = _valid_date(damage.get("Termin"))
    if day is None:
        return None
    match = re.search(r"(\d{1,2}):(\d{2})", damage.get("ZeitscheibeText") or "")
    start = time(int(match[1]), int(match[2])) if match else time()
    return datetime.combine(date.fromisoformat(day), start, dt_util.get_default_time_zone())


def next_appointment(data: VivawestData) -> datetime | None:
    today = dt_util.now().replace(hour=0, minute=0, second=0, microsecond=0)
    upcoming = [t for d in data.open_damages or [] if (t := damage_appointment(d)) and t >= today]
    return min(upcoming, default=None)


def damages_attributes(data: VivawestData) -> dict[str, Any]:
    reports = []
    for d in data.open_damages or []:
        termin = damage_appointment(d)
        reports.append({
            "schaden": d.get("Schadensbeschreibung"),
            "raum": d.get("Raum") or None,
            "status": d.get("StatusTitle"),
            "gemeldet_am": _valid_date(d.get("Anlagedatum")),
            "termin": termin.isoformat() if termin else None,
        })
    return {"meldungen": reports}


def _count(value: list[Any] | None) -> int | None:
    return None if value is None else len(value)


# ---------------------------------------------------------------------------
# Beschreibungen
# ---------------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class VivawestSensorDescription(SensorEntityDescription):
    value_fn: Callable[[VivawestData], Any]
    attrs_fn: Callable[[VivawestData], dict[str, Any]] = lambda _: {}
    # Platzhalter im übersetzten Namen (z. B. Raum des Zählers)
    placeholders: dict[str, str] | None = None
    # True: kein Wert ist "unbekannt" statt "nicht verfügbar"
    allow_none: bool = False


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
    _cost("tv_versorgung", "3760"),
    VivawestSensorDescription(
        key="ungelesene_nachrichten",
        translation_key="ungelesene_nachrichten",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.unread_messages,
    ),
    VivawestSensorDescription(
        key="portal_neuigkeiten",
        translation_key="portal_neuigkeiten",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.notifications,
    ),
    VivawestSensorDescription(
        key="offene_schadensmeldungen",
        translation_key="offene_schadensmeldungen",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _count(d.open_damages),
        attrs_fn=damages_attributes,
    ),
    VivawestSensorDescription(
        key="naechster_handwerkertermin",
        translation_key="naechster_handwerkertermin",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=next_appointment,
        allow_none=True,
    ),
)


def meter_descriptions(data: VivawestData) -> list[VivawestSensorDescription]:
    """Ein Sensor je Einzelzähler (Zählerstand), bei Warmwasser zusätzlich in kWh."""
    result = []
    for uvi_type, meters in data.meters.items():
        rooms = [m["geraet"].get("raum") for m in meters]
        for meter in meters:
            geraet = meter["geraet"]
            meter_id = geraet.get("id")
            if meter_id is None:
                continue
            room = geraet.get("raum") or str(geraet.get("nr") or meter_id)
            if rooms.count(geraet.get("raum")) > 1:  # mehrere Zähler im selben Raum
                room = f"{room} {geraet.get('nr') or meter_id}"
            unit, device_class = METER_UNITS.get(geraet.get("einheit"), ("VE", None))
            result.append(VivawestSensorDescription(
                key=f"zaehler_{meter_id}",
                translation_key=f"zaehler_{uvi_type}",
                placeholders={"raum": room},
                native_unit_of_measurement=unit,
                device_class=device_class,
                state_class=SensorStateClass.TOTAL_INCREASING,
                value_fn=lambda d, i=meter_id: meter_value(d, i, "verbrauchswert"),
                attrs_fn=lambda d, i=meter_id: meter_attributes(
                    d, i, "verbrauchswert_delta_monatsende"),
            ))
            if any(m.get("verbrauchswert_kwh") is not None for m in meter["messwerte"]):
                result.append(VivawestSensorDescription(
                    key=f"zaehler_{meter_id}_energie",
                    translation_key=f"zaehler_{uvi_type}_energie",
                    placeholders={"raum": room},
                    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                    device_class=SensorDeviceClass.ENERGY,
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    value_fn=lambda d, i=meter_id: meter_value(d, i, "verbrauchswert_kwh"),
                    attrs_fn=lambda d, i=meter_id: meter_attributes(
                        d, i, "verbrauchswert_delta_monatsende_kwh"),
                ))
    return result


def settlement_descriptions(data: VivawestData) -> list[VivawestSensorDescription]:
    """Ein Sensor je Abrechnungsart (Betriebs-, Heiz-, Wasserkosten)."""
    return [
        VivawestSensorDescription(
            key=f"abrechnung_{slugify(kind)}",
            translation_key="abrechnung",
            placeholders={"art": kind},
            native_unit_of_measurement=CURRENCY_EURO,
            device_class=SensorDeviceClass.MONETARY,
            suggested_display_precision=2,
            value_fn=lambda d, k=kind: settlement_value(d, k),
            attrs_fn=lambda d, k=kind: settlement_attributes(d, k),
        )
        for kind in data.settlements
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VivawestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_entities() -> None:
        """Feste Sensoren sowie neu hinzugekommene Zähler und Abrechnungen anlegen."""
        if coordinator.data is None:
            return
        descriptions = [
            *SENSORS,
            *meter_descriptions(coordinator.data),
            *settlement_descriptions(coordinator.data),
        ]
        new = [d for d in descriptions if d.key not in known]
        known.update(d.key for d in new)
        if new:
            async_add_entities(VivawestSensor(coordinator, d) for d in new)

    add_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_entities))


class VivawestSensor(CoordinatorEntity[VivawestCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: VivawestSensorDescription

    def __init__(self, coordinator: VivawestCoordinator,
                 description: VivawestSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        if description.placeholders:
            self._attr_translation_placeholders = description.placeholders
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
        return super().available and (
            self.entity_description.allow_none or self.native_value is not None
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self.coordinator.data)
