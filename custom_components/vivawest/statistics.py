"""Rückwirkender Import der Monatsverbräuche in die Langzeitstatistik.

Vivawest liefert einen Wert pro Monat (nach Monatsende). Er wird gleichmäßig auf
alle Stunden des Monats verteilt und als externe Statistik ``vivawest:<key>``
importiert. Welche Monate neu geschrieben werden müssen, ergibt sich aus dem
Vergleich mit den Monatssummen, die bereits im Recorder liegen. Ab dem ersten
neuen oder geänderten Monat wird alles neu berechnet, damit die Summe stetig bleibt.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter

from .const import (
    CONF_PRICE_HEATING,
    CONF_PRICE_HOT_WATER_ENERGY,
    CONF_PRICE_WATER,
    DOMAIN,
    LOGGER,
)

# Abweichung, ab der ein Monat als geändert gilt (Werte haben 2 Nachkommastellen)
TOLERANCE = 0.005

Month = tuple[int, int]  # (jahr, monat)


@dataclass(frozen=True)
class StatDef:
    key: str
    name: str
    unit: str
    unit_class: str
    uvi_type: str
    field: str
    price_key: str  # Option mit dem Preis pro Einheit

    @property
    def statistic_id(self) -> str:
        return f"{DOMAIN}:{self.key}"


STATISTICS: tuple[StatDef, ...] = (
    StatDef("kaltwasser", "Vivawest Kaltwasser", UnitOfVolume.CUBIC_METERS,
            VolumeConverter.UNIT_CLASS, "kaltwasser", "verbrauchswert", CONF_PRICE_WATER),
    StatDef("warmwasser", "Vivawest Warmwasser", UnitOfVolume.CUBIC_METERS,
            VolumeConverter.UNIT_CLASS, "warmwasser", "verbrauchswert", CONF_PRICE_WATER),
    StatDef("warmwasser_energie", "Vivawest Warmwasser Energie", UnitOfEnergy.KILO_WATT_HOUR,
            EnergyConverter.UNIT_CLASS, "warmwasser", "kwh", CONF_PRICE_HOT_WATER_ENERGY),
    StatDef("heizenergie", "Vivawest Heizenergie", UnitOfEnergy.KILO_WATT_HOUR,
            EnergyConverter.UNIT_CLASS, "heizenergie", "kwh", CONF_PRICE_HEATING),
)


# ---------------------------------------------------------------------------
# Reine Hilfsfunktionen (ohne Home Assistant testbar)
# ---------------------------------------------------------------------------
def month_values(history: list[dict[str, Any]], field: str) -> dict[Month, float]:
    """{(jahr, monat): wert} aus einem Monatsverlauf; fehlende Werte werden ausgelassen."""
    result: dict[Month, float] = {}
    for entry in history:
        value, year, month = entry.get(field), entry.get("jahr"), entry.get("monat")
        if value is None or not year or not month:
            continue
        result[(int(year), int(month))] = float(value)
    return result


def month_start(month: Month, tz: tzinfo) -> datetime:
    return datetime(month[0], month[1], 1, tzinfo=tz)


def next_month(month: Month) -> Month:
    return (month[0] + month[1] // 12, month[1] % 12 + 1)


def month_hours(month: Month, tz: tzinfo) -> list[datetime]:
    """Alle Stundenanfänge eines Monats (sommer-/winterzeitfest, in UTC)."""
    cur = month_start(month, tz).astimezone(timezone.utc)
    end = month_start(next_month(month), tz).astimezone(timezone.utc)
    hours = []
    while cur < end:
        hours.append(cur)
        cur += timedelta(hours=1)
    return hours


def distribute(month: Month, value: float, start_sum: float, tz: tzinfo) -> list[StatisticData]:
    """Verteilt ``value`` gleichmäßig auf die Stunden des Monats."""
    hours = month_hours(month, tz)
    per_hour = value / len(hours)
    rows: list[StatisticData] = []
    for i, hour in enumerate(hours, start=1):
        total = round(start_sum + per_hour * i, 6)
        rows.append(StatisticData(start=hour, state=total, sum=total))
    return rows


def plan_import(
    values: dict[Month, float], existing_sums: dict[Month, float]
) -> tuple[list[tuple[Month, float]], float]:
    """Bestimmt die (neu) zu importierenden Monate.

    ``values``: Monatsverbräuche aus der API.
    ``existing_sums``: kumulierte Summe am Ende jedes Monats laut Recorder.
    Rückgabe: ([(monat, wert), ...] aufsteigend, Startsumme vor dem ersten Monat).
    """
    def sum_before(month: Month) -> float:
        earlier = [m for m in existing_sums if m < month]
        return existing_sums[max(earlier)] if earlier else 0.0

    first_changed: Month | None = None
    for month, value in sorted(values.items()):
        if month not in existing_sums:
            first_changed = month
            break
        stored_value = existing_sums[month] - sum_before(month)
        if abs(stored_value - value) > TOLERANCE:
            first_changed = month
            break
    if first_changed is None:
        return [], 0.0

    plan = [(m, v) for m, v in sorted(values.items()) if m >= first_changed]
    return plan, sum_before(first_changed)


# ---------------------------------------------------------------------------
# Recorder-Zugriff
# ---------------------------------------------------------------------------
async def _existing_month_sums(
    hass: HomeAssistant, statistic_id: str, since: datetime, tz: tzinfo
) -> dict[Month, float]:
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, since, None, {statistic_id}, "month", None, {"sum"}
    )
    result: dict[Month, float] = {}
    for row in rows.get(statistic_id, []):
        if row.get("sum") is None:
            continue
        start = datetime.fromtimestamp(row["start"], tz)
        result[(start.year, start.month)] = float(row["sum"])
    return result


async def async_read_monthly_values(hass: HomeAssistant, years: int = 3) -> dict[str, dict[Month, float]]:
    """Monatsverbräuche aller Statistiken aus dem Recorder ({key: {(jahr, monat): wert}}).

    Ergänzt den Verlauf des Portals (ca. 12 Monate) um ältere Monate, z. B. für den
    Vorjahresvergleich der Abrechnungsprognose. Monatswert = Differenz der Summen
    aufeinanderfolgender Monate; Monate ohne Vormonat entfallen.
    """
    tz = dt_util.get_default_time_zone()
    now = dt_util.now(tz)
    since = month_start((now.year - years, now.month), tz)
    result: dict[str, dict[Month, float]] = {}
    for stat in STATISTICS:
        sums = await _existing_month_sums(hass, stat.statistic_id, since, tz)
        result[stat.key] = {
            month: round(total - sums[prev], 4)
            for month, total in sums.items()
            if (prev := (month[0] - (month[1] == 1), (month[1] - 2) % 12 + 1)) in sums
        }
    return result


async def async_import_statistics(
    hass: HomeAssistant,
    history: dict[str, list[dict[str, Any]]],
    prices: Mapping[str, Any] | None = None,
) -> None:
    """Importiert neue/geänderte Monate aller Statistiken.

    Ist für eine Statistik ein Preis gesetzt, entsteht zusätzlich ``<id>_kosten``
    (Monatswert × Preis). Das Energie-Dashboard rechnet für externe Statistiken
    selbst keine Kosten, kann aber eine Kostenstatistik verwenden.
    """
    tz = dt_util.get_default_time_zone()
    prices = prices or {}
    for stat in STATISTICS:
        values = month_values(history.get(stat.uvi_type, []), stat.field)
        if not values:
            continue
        await _import_series(
            hass, tz, values,
            StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=stat.name,
                source=DOMAIN,
                statistic_id=stat.statistic_id,
                unit_class=stat.unit_class,
                unit_of_measurement=stat.unit,
            ),
        )
        price = prices.get(stat.price_key)
        if not price:
            continue
        await _import_series(
            hass, tz, {m: round(v * float(price), 4) for m, v in values.items()},
            StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=f"{stat.name} Kosten",
                source=DOMAIN,
                statistic_id=f"{stat.statistic_id}_kosten",
                unit_class=None,
                unit_of_measurement=hass.config.currency,
            ),
        )


async def _import_series(
    hass: HomeAssistant, tz: tzinfo, values: dict[Month, float], metadata: StatisticMetaData
) -> None:
    """Schreibt die Monatswerte einer Statistik ab dem ersten neuen/geänderten Monat."""
    statistic_id = metadata["statistic_id"]
    # Ein Jahr Puffer vor dem ältesten API-Monat, um die Startsumme zu finden
    oldest = min(values)
    since = month_start((oldest[0] - 1, oldest[1]), tz)
    existing = await _existing_month_sums(hass, statistic_id, since, tz)

    plan, start_sum = plan_import(values, existing)
    if not plan:
        LOGGER.debug("Statistik %s ist aktuell", statistic_id)
        return

    rows: list[StatisticData] = []
    for month, value in plan:
        rows.extend(distribute(month, value, start_sum, tz))
        start_sum += value
    async_add_external_statistics(hass, metadata, rows)
    LOGGER.info(
        "Statistik %s: %d Monat(e) ab %02d/%d importiert",
        statistic_id, len(plan), plan[0][0][1], plan[0][0][0],
    )
