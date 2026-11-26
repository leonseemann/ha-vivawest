"""Tests für die Prognose der nächsten Nebenkostenabrechnung."""
from __future__ import annotations

from datetime import date

import pytest

from custom_components.vivawest.forecast import (
    FORECAST_KINDS,
    HEATING_PROFILE,
    billing_period,
    calculate,
    prepayments,
    project,
)

from .conftest import make_data

BK, HK = FORECAST_KINDS
TODAY = date(2026, 11, 23)
MONTHS_2026 = [(2026, m) for m in range(1, 13)]
WATER = {"preis_wasser": 5.0}
HEAT = {"preis_heizenergie": 0.12, "preis_warmwasser_energie": 0.1}


def _with_water_position(data):
    """Betriebskosten: 20 % Wasser (391,50 von 1957,50 €)."""
    data.settlements["Betriebskosten"]["Nebenkostenschluessel"]["results"].append(
        {"Nksl": "2200", "NkslText": "Wasserversorgung/Entwässerung", "KostenNkslMv": 391.5})
    return data


def _heating_settlement(**kwargs) -> dict:
    return {"AeErgebnis": 80.0, "KostenMv": 1500.0, "KostenMvPrognose": 1600.0,
            "AbrechnungVon": "2025-01-01T00:00:00+01:00",
            "AbrechnungBis": "2025-12-31T00:00:00+01:00"} | kwargs


def test_billing_period() -> None:
    assert billing_period([{"AbrechnungBis": "2025-06-30T00:00:00+02:00"}]) == (
        date(2025, 7, 1), date(2026, 6, 30))
    assert billing_period([{"AbrechnungBis": "-001-11-30T00:00:00+00:53"}]) is None


def test_prepayments_with_future_change_and_missing_history() -> None:
    monthly = prepayments(make_data().conditions, "4000", MONTHS_2026)
    # Jan–Apr fehlen in den Konditionen → Betrag des ersten bekannten Zeitraums,
    # ab Dezember gilt der angekündigte neue Betrag.
    assert monthly[(2026, 1)] == 150.0 and monthly[(2026, 12)] == 140.0
    assert sum(monthly.values()) == 11 * 150 + 140
    assert prepayments(make_data().conditions, "9999", MONTHS_2026) is None


def test_project_linear_and_seasonal() -> None:
    values = {(2026, 8): 3.0, (2026, 9): 1.0, (2026, 10): 2.0}
    assert project(values, MONTHS_2026, seasonal=False) == (6.0, 18.0)
    # Heizung: Aug–Okt sind 123,3 ‰ des Jahresbedarfs
    actual, rest = project(values, MONTHS_2026, seasonal=True)
    assert rest == pytest.approx(6.0 / 123.3 * 876.6)
    # Nur Sommermonate → keine belastbare Hochrechnung
    assert project({(2026, 7): 1.0, (2026, 8): 1.0}, MONTHS_2026, seasonal=True) is None
    assert sum(HEATING_PROFILE.values()) == pytest.approx(1000, abs=0.2)


def test_operating_costs_without_price_uses_vivawest_forecast() -> None:
    fc = calculate(make_data(), BK, TODAY)
    assert fc.method == "vivawest"
    assert (fc.period_from, fc.period_to) == (date(2026, 1, 1), date(2026, 12, 31))
    assert fc.expected_costs == 1980.0 and fc.prepayments_total == 1790.0
    assert fc.prepayments_paid == 1650.0
    assert fc.result == 190.0 and fc.balance_so_far is None


def test_operating_costs_from_consumption() -> None:
    fc = calculate(_with_water_position(make_data()), BK, TODAY, WATER)
    assert fc.method == "verbrauch"
    assert fc.fixed_costs == pytest.approx(1980 * 0.8)
    # Aug–Okt: Kaltwasser 8,5 m³ + Warmwasser 2,8 m³, Rest des Jahres gleichmäßig
    assert fc.consumption_costs_so_far == pytest.approx(11.3 * 5)
    assert fc.consumption_costs_rest == pytest.approx(11.3 * 3 * 5)
    assert fc.expected_costs == pytest.approx(1584 + 56.5 + 169.5)
    assert fc.result == pytest.approx(1810 - 1790)
    # Aug–Okt: 3/12 der Fixkosten + echte Wasserkosten − 3 × 150 € Vorauszahlung
    assert fc.months_with_data == 3
    assert fc.balance_so_far == pytest.approx(396 + 56.5 - 450)


def test_water_settlement_is_fully_consumption_based() -> None:
    data = make_data()
    data.settlements["Wasserkosten"] = {"KostenMv": 300.0, "AbrechnungBis": "2025-12-31"}
    fc = calculate(data, BK, TODAY, WATER)
    # Betriebskosten ohne Wasserposition: komplett fix; Wasserkosten: komplett aus Verbrauch
    assert fc.fixed_costs == 1980.0
    assert fc.expected_costs == pytest.approx(1980 + 11.3 * 4 * 5)


def test_heating_costs_from_consumption() -> None:
    data = make_data()
    data.settlements["Heiz- und Warmwasserkosten"] = _heating_settlement()
    fc = calculate(data, HK, TODAY, HEAT)
    assert fc.method == "verbrauch"
    heating_rest = 82 / 123.3 * 876.6
    expected = 1600 * 0.3 + (82 + heating_rest) * 0.12 + (160 + 480) * 0.1
    assert fc.expected_costs == pytest.approx(expected, abs=0.01)
    assert fc.prepayments_total == 12 * 120.0
    assert fc.result == pytest.approx(expected - 1440, abs=0.01)


def test_heating_missing_price_falls_back() -> None:
    data = make_data()
    data.settlements["Heizkosten"] = _heating_settlement()
    fc = calculate(data, HK, TODAY, {"preis_heizenergie": 0.12})  # Warmwasserpreis fehlt
    assert fc.method == "vivawest"
    assert fc.expected_costs == 1600.0 and fc.result == 160.0


def test_heating_only_summer_months_falls_back() -> None:
    data = make_data()
    data.settlements["Heizkosten"] = _heating_settlement()
    data.history["heizenergie"] = [e for e in data.history["heizenergie"] if e["monat"] != 10]
    assert calculate(data, HK, TODAY, HEAT).method == "vivawest"


def test_no_settlement() -> None:
    assert calculate(make_data(), HK, TODAY) is None
