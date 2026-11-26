"""Prognose der nächsten Nebenkostenabrechnung: Nachzahlung oder Guthaben.

Rechnung je Vorauszahlungsart (Betriebskosten, Heizkosten) für den laufenden
Abrechnungszeitraum, also das Jahr nach der neuesten Abrechnung:

    Ergebnis = Fixkosten + Verbrauchskosten − Vorauszahlungen   (> 0 Nachzahlung)

- Vorauszahlungen: für jeden Monat der dann gültige Betrag laut Mietkonditionen
  (inklusive angekündigter Änderungen).
- Verbrauchskosten: tatsächlicher Monatsverbrauch des laufenden Zeitraums × Preis aus
  den Optionen. Monate ohne Werte (Rest des Jahres) werden aus dem bisherigen Verbrauch
  hochgerechnet, Heizung nach der Gradtagszahlen-Verteilung, Wasser gleichmäßig.
- Fixkosten: kommen in keinen Verbrauchsdaten vor (Grundsteuer, Versicherung,
  Grundkosten der Heizung …) und stammen deshalb aus der Prognose von Vivawest in der
  letzten Abrechnung (sonst deren Kosten): Heizkosten 30 % (HeizkostenV § 7: 50–70 %
  nach Verbrauch, bei älteren Gebäuden 70 %), Betriebskosten alles außer den
  Wasser-Positionen.
- Fehlt ein nötiger Preis oder gibt es noch zu wenig Verbrauchsdaten, gilt die Prognose
  von Vivawest unverändert (``methode = "vivawest"``).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import Any

from .api import VivawestData
from .const import CONF_PRICE_HEATING, CONF_PRICE_HOT_WATER_ENERGY, CONF_PRICE_WATER
from .statistics import STATISTICS, month_values

Month = tuple[int, int]

# Anteil der Heizkosten, der nach Verbrauch verteilt wird
HEATING_CONSUMPTION_SHARE = 0.7
# Gradtagszahlen-Verteilung (Promille des Jahres-Heizbedarfs je Monat), wie sie bei
# Zwischenablesungen in Heizkostenabrechnungen üblich ist
HEATING_PROFILE = {1: 170, 2: 150, 3: 130, 4: 80, 5: 40, 6: 13.3, 7: 13.3, 8: 13.3,
                   9: 30, 10: 80, 11: 120, 12: 160}
# Mindestanteil am Jahres-Heizbedarf, den die Monate mit Werten abdecken müssen,
# damit die Hochrechnung belastbar ist (nur Sommermonate reichen nicht)
MIN_HEATING_SHARE = 100


@dataclass(frozen=True, kw_only=True)
class Series:
    stat_key: str  # Key in STATISTICS
    price_key: str  # Option mit dem Preis pro Einheit
    seasonal: bool  # Heizung: Hochrechnung nach Gradtagszahlen


@dataclass(frozen=True, kw_only=True)
class ForecastKind:
    key: str
    # Vorauszahlungs-Kondition (Condtype)
    condtype: str
    # Abrechnungsarten mit "heiz" im Namen gehören zu den Heizkosten, alle übrigen
    # (Betriebs-, Wasserkosten) zu den Betriebskosten.
    heating: bool
    series: tuple[Series, ...]


FORECAST_KINDS = (
    ForecastKind(key="prognose_betriebskosten", condtype="4000", heating=False, series=(
        Series(stat_key="kaltwasser", price_key=CONF_PRICE_WATER, seasonal=False),
        Series(stat_key="warmwasser", price_key=CONF_PRICE_WATER, seasonal=False),
    )),
    ForecastKind(key="prognose_heizkosten", condtype="4010", heating=True, series=(
        Series(stat_key="heizenergie", price_key=CONF_PRICE_HEATING, seasonal=True),
        Series(stat_key="warmwasser_energie", price_key=CONF_PRICE_HOT_WATER_ENERGY, seasonal=False),
    )),
)


@dataclass
class Forecast:
    result: float
    period_from: date
    period_to: date
    method: str  # "verbrauch" | "vivawest"
    expected_costs: float
    fixed_costs: float
    consumption_costs_so_far: float | None
    consumption_costs_rest: float | None
    months_with_data: int
    # Kosten minus Vorauszahlungen der Monate mit Verbrauchswerten (> 0 = im Rückstand)
    balance_so_far: float | None
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


def _months(start: date, end: date) -> list[Month]:
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


def prepayments(conditions: list[dict[str, Any]], condtype: str,
                months: list[Month]) -> dict[Month, float] | None:
    """Vorauszahlung je Monat. Monate vor dem ersten bekannten Konditionszeitraum erhalten
    dessen Betrag (das Portal liefert nicht immer die Historie)."""
    if not any(i.get("Condtype") == condtype for p in conditions
               for i in (p.get("ConditionItems") or {}).get("results") or []):
        return None
    amounts = [monthly_prepayment(conditions, condtype, y, m) for y, m in months]
    known = [a for a in amounts if a is not None]
    if not known:
        return None
    return {month: known[0] if a is None else a for month, a in zip(months, amounts)}


def series_values(data: VivawestData, stat_key: str) -> dict[Month, float]:
    """Monatsverbrauch einer Statistik aus dem Portal-Verlauf."""
    stat = next(s for s in STATISTICS if s.key == stat_key)
    return month_values(data.history.get(stat.uvi_type) or [], stat.field)


def project(values: dict[Month, float], months: list[Month],
            seasonal: bool) -> tuple[float, float] | None:
    """(Verbrauch bisher, hochgerechneter Verbrauch der Monate ohne Wert) im Zeitraum.

    None, wenn die vorhandenen Monate keine belastbare Hochrechnung erlauben.
    """
    known = [m for m in months if m in values]
    missing = [m for m in months if m not in values]
    actual = sum(values[m] for m in known)
    if not missing:
        return actual, 0.0
    if seasonal:
        share = sum(HEATING_PROFILE[m] for _, m in known)
        if share < MIN_HEATING_SHARE:
            return None
        return actual, actual / share * sum(HEATING_PROFILE[m] for _, m in missing)
    if not known:
        return None
    return actual, actual / len(known) * len(missing)


def _fixed_share(kind: ForecastKind, name: str, settlement: dict[str, Any]) -> float:
    """Anteil der Kosten einer Abrechnungsart, der nicht vom Verbrauch abhängt."""
    if kind.heating:
        return 1 - HEATING_CONSUMPTION_SHARE
    if "wasser" in name.lower():
        return 0.0
    costs = _float(settlement.get("KostenMv"))
    positions = (settlement.get("Nebenkostenschluessel") or {}).get("results") or []
    water = sum(_float(p.get("KostenNkslMv")) for p in positions
                if "wasser" in (p.get("NkslText") or "").lower())
    return max(1 - water / costs, 0.0) if costs > 0 else 1.0


def calculate(data: VivawestData, kind: ForecastKind, today: date,
              prices: Mapping[str, Any] | None = None) -> Forecast | None:
    """Prognose für eine Vorauszahlungsart; None, wenn Abrechnung oder Konditionen fehlen."""
    prices = prices or {}
    settlements = {
        name: st for name, st in data.settlements.items()
        if ("heiz" in name.lower()) == kind.heating
    }
    period = billing_period(list(settlements.values()))
    if period is None:
        return None
    months = _months(*period)
    monthly = prepayments(data.conditions, kind.condtype, months)
    if monthly is None:
        return None

    basis = fixed = 0.0
    for name, st in settlements.items():
        costs = _float(st.get("KostenMvPrognose")) or _float(st.get("KostenMv"))
        basis += costs
        fixed += costs * _fixed_share(kind, name, st)
    if basis <= 0:
        return None

    # Verbrauchskosten aus den echten Monatswerten (nur Statistiken mit Daten)
    so_far = rest = 0.0
    data_months: set[Month] | None = None
    method = "verbrauch"
    for series in kind.series:
        values = {m: v for m, v in series_values(data, series.stat_key).items() if m in monthly}
        if not values:
            continue
        price = _float(prices.get(series.price_key))
        projection = project(values, months, series.seasonal)
        if price <= 0 or projection is None:
            method = "vivawest"
            break
        so_far += projection[0] * price
        rest += projection[1] * price
        data_months = set(values) if data_months is None else data_months & set(values)
    if data_months is None:  # gar keine Verbrauchsdaten im Zeitraum
        method = "vivawest"

    total_prepaid = sum(monthly.values())
    paid = sum(a for (y, m), a in monthly.items() if date(y, m, 1) <= today)
    if method == "vivawest":
        expected, balance, so_far_out, rest_out, n_data = basis, None, None, None, 0
    else:
        expected = fixed + so_far + rest
        n_data = len(data_months)
        # Stand der Monate mit Verbrauchswerten: anteilige Fixkosten + echte Verbrauchskosten
        balance = round(fixed * n_data / len(months) + so_far
                        - sum(monthly[m] for m in data_months), 2)
        so_far_out, rest_out = round(so_far, 2), round(rest, 2)

    return Forecast(
        result=round(expected - total_prepaid, 2),
        period_from=period[0],
        period_to=period[1],
        method=method,
        expected_costs=round(expected, 2),
        fixed_costs=round(fixed if method == "verbrauch" else basis, 2),
        consumption_costs_so_far=so_far_out,
        consumption_costs_rest=rest_out,
        months_with_data=n_data,
        balance_so_far=balance,
        prepayments_total=round(total_prepaid, 2),
        prepayments_paid=round(paid, 2),
        months=len(months),
    )
