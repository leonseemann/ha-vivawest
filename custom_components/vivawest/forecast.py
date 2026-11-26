"""Prognose der nächsten Nebenkostenabrechnung: Nachzahlung oder Guthaben.

Rechnung je Vorauszahlungsart (Betriebskosten, Heizkosten) für den laufenden
Abrechnungszeitraum, also das Jahr nach der neuesten Abrechnung:

    Ergebnis = erwartete Kosten − Vorauszahlungen im Zeitraum   (> 0 Nachzahlung)

- Erwartete Kosten: Prognose von Vivawest aus der letzten Abrechnung
  (``KostenMvPrognose``), sonst die Kosten der letzten Abrechnung.
- Der verbrauchsabhängige Teil wird mit dem Verbrauch im Vorjahresvergleich
  skaliert (gleiche Monate des laufenden Zeitraums gegen das Vorjahr).
  Heizkosten: 70 % verbrauchsabhängig (HeizkostenV § 7, 50–70 %, bei älteren
  Gebäuden 70 %). Betriebskosten: nur die Wasser-Positionen der Abrechnung.
- Vorauszahlungen: Monatsbeträge aus den Mietkonditionen, für jeden Monat des
  Zeitraums der dann gültige Betrag (auch künftige, bereits angekündigte Änderungen).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import Any

from .api import VivawestData
from .statistics import STATISTICS, month_values

# Anteil der Heizkosten, der nach Verbrauch verteilt wird
HEATING_CONSUMPTION_SHARE = 0.7
# Mindestanzahl vergleichbarer Monate für die Verbrauchsanpassung
MIN_COMPARE_MONTHS = 2


@dataclass(frozen=True, kw_only=True)
class ForecastKind:
    key: str
    # Vorauszahlungs-Kondition (Condtype)
    condtype: str
    # Abrechnungsarten mit "heiz" im Namen gehören zu den Heizkosten, alle übrigen
    # (Betriebs-, Wasserkosten) zu den Betriebskosten.
    heating: bool
    # Verbrauch für den Vorjahresvergleich: Statistik-Keys aus STATISTICS
    consumption: tuple[str, ...]


FORECAST_KINDS = (
    ForecastKind(key="prognose_betriebskosten", condtype="4000", heating=False,
                 consumption=("kaltwasser", "warmwasser")),
    ForecastKind(key="prognose_heizkosten", condtype="4010", heating=True,
                 consumption=("heizenergie", "warmwasser_energie")),
)


@dataclass
class Forecast:
    result: float
    period_from: date
    period_to: date
    expected_costs: float
    cost_basis: float
    cost_basis_source: str  # "prognose" | "vorjahr"
    consumption_factor: float | None
    compared_months: int
    prepayments_total: float
    prepayments_paid: float
    months: int


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _date(value: Any) -> date | None:
    if isinstance(value, str) and re.match(r"^(19|20)\d\d-\d\d-\d\d", value):
        return date.fromisoformat(value[:10])
    return None


def _months(start: date, end: date) -> list[tuple[int, int]]:
    """(Jahr, Monat) aller Monate von start bis end."""
    result = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        result.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def billing_period(settlements: list[dict[str, Any]]) -> tuple[date, date] | None:
    """Laufender Zeitraum: ein Jahr ab dem Tag nach der neuesten Abrechnung."""
    ends = [d for st in settlements if (d := _date(st.get("AbrechnungBis")))]
    if not ends:
        return None
    start = max(ends) + timedelta(days=1)
    try:
        end = start.replace(year=start.year + 1) - timedelta(days=1)
    except ValueError:  # 29. Februar
        end = start.replace(year=start.year + 1, day=28)
    return start, end


def monthly_prepayment(conditions: list[dict[str, Any]], condtype: str,
                       year: int, month: int) -> float | None:
    """Vorauszahlung eines Monats laut Mietkonditionen; None = kein Zeitraum bekannt."""
    day = date(year, month, 1).isoformat()
    for period in conditions:
        if (period.get("GueltigVon") or "") <= day <= (period.get("GueltigBis") or "9999"):
            items = (period.get("ConditionItems") or {}).get("results") or []
            return sum(_float(i.get("GrossAmountdec")) for i in items if i.get("Condtype") == condtype)
    return None


def prepayments(conditions: list[dict[str, Any]], condtype: str, months: list[tuple[int, int]],
                today: date) -> tuple[float, float] | None:
    """(Summe im Zeitraum, davon bis heute fällig). Monate vor dem ersten bekannten
    Konditionszeitraum erhalten dessen Betrag (das Portal liefert nicht immer die Historie)."""
    if not any(i.get("Condtype") == condtype for p in conditions
               for i in (p.get("ConditionItems") or {}).get("results") or []):
        return None
    amounts = [monthly_prepayment(conditions, condtype, y, m) for y, m in months]
    known = [a for a in amounts if a is not None]
    if not known:
        return None
    first = known[0]
    amounts = [first if a is None else a for a in amounts]
    paid = sum(a for (y, m), a in zip(months, amounts) if date(y, m, 1) <= today)
    return round(sum(amounts), 2), round(paid, 2)


def _consumption(data: VivawestData, keys: tuple[str, ...]) -> dict[tuple[int, int], float]:
    """Monatsverbrauch (Summe der Statistiken); nur Monate, in denen alle einen Wert haben.

    Basis ist die Langzeitstatistik, überschrieben vom aktuellen Portal-Verlauf.
    """
    per_key = []
    for stat in STATISTICS:
        if stat.key not in keys:
            continue
        values = dict(data.archive.get(stat.key) or {})
        values.update(month_values(data.history.get(stat.uvi_type) or [], stat.field))
        if values:
            per_key.append(values)
    if not per_key:
        return {}
    common = set.intersection(*(set(v) for v in per_key))
    return {k: sum(v[k] for v in per_key) for k in common}


def consumption_factor(data: VivawestData, keys: tuple[str, ...],
                       months: list[tuple[int, int]]) -> tuple[float | None, int]:
    """Verbrauch der bisherigen Monate des Zeitraums ÷ Verbrauch derselben Monate im Vorjahr."""
    values = _consumption(data, keys)
    pairs = [(values[(y, m)], values[(y - 1, m)]) for y, m in months
             if (y, m) in values and (y - 1, m) in values]
    previous = sum(p for _, p in pairs)
    if len(pairs) < MIN_COMPARE_MONTHS or previous <= 0:
        return None, len(pairs)
    return sum(c for c, _ in pairs) / previous, len(pairs)


def _variable_share(kind: ForecastKind, name: str, settlement: dict[str, Any]) -> float:
    """Anteil der Kosten einer Abrechnungsart, der vom Verbrauch abhängt."""
    if kind.heating:
        return HEATING_CONSUMPTION_SHARE
    if "wasser" in name.lower():
        return 1.0
    costs = _float(settlement.get("KostenMv"))
    positions = (settlement.get("Nebenkostenschluessel") or {}).get("results") or []
    water = sum(_float(p.get("KostenNkslMv")) for p in positions
                if "wasser" in (p.get("NkslText") or "").lower())
    return min(water / costs, 1.0) if costs > 0 else 0.0


def calculate(data: VivawestData, kind: ForecastKind, today: date) -> Forecast | None:
    """Prognose für eine Vorauszahlungsart; None, wenn Abrechnung oder Konditionen fehlen."""
    settlements = {
        name: st for name, st in data.settlements.items()
        if ("heiz" in name.lower()) == kind.heating
    }
    period = billing_period(list(settlements.values()))
    if period is None:
        return None
    months = _months(*period)
    paid = prepayments(data.conditions, kind.condtype, months, today)
    if paid is None:
        return None

    factor, compared = consumption_factor(data, kind.consumption, months)
    basis = expected = 0.0
    from_forecast = False
    for name, st in settlements.items():
        costs = _float(st.get("KostenMvPrognose"))
        if costs > 0:
            from_forecast = True
        else:
            costs = _float(st.get("KostenMv"))
        basis += costs
        share = _variable_share(kind, name, st)
        expected += costs * (1 - share + share * (factor if factor is not None else 1.0))
    if basis <= 0:
        return None

    total, paid_so_far = paid
    return Forecast(
        result=round(expected - total, 2),
        period_from=period[0],
        period_to=period[1],
        expected_costs=round(expected, 2),
        cost_basis=round(basis, 2),
        cost_basis_source="prognose" if from_forecast else "vorjahr",
        consumption_factor=factor,
        compared_months=compared,
        prepayments_total=total,
        prepayments_paid=paid_so_far,
        months=len(months),
    )
