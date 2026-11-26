"""Tests für die Prognose der nächsten Nebenkostenabrechnung."""
from __future__ import annotations

from datetime import date

import pytest

from custom_components.vivawest.forecast import (
    FORECAST_KINDS,
    billing_period,
    calculate,
    prepayments,
)

from .conftest import make_data

BK, HK = FORECAST_KINDS
TODAY = date(2026, 11, 23)


def _heating_settlement(**kwargs) -> dict:
    return {"AeErgebnis": 80.0, "KostenMv": 1500.0, "Prepayments": 1420.0,
            "AbrechnungVon": "2025-01-01T00:00:00+01:00",
            "AbrechnungBis": "2025-12-31T00:00:00+01:00"} | kwargs


def test_billing_period() -> None:
    assert billing_period([{"AbrechnungBis": "2025-06-30T00:00:00+02:00"}]) == (
        date(2025, 7, 1), date(2026, 6, 30))
    assert billing_period([{"AbrechnungBis": "-001-11-30T00:00:00+00:53"}]) is None


def test_prepayments_with_future_change_and_missing_history() -> None:
    data = make_data()
    months = [(2026, m) for m in range(1, 13)]
    # Jan–Apr fehlen in den Konditionen → Betrag des ersten bekannten Zeitraums (150 €),
    # ab Dezember gilt der angekündigte neue Betrag (140 €).
    assert prepayments(data.conditions, "4000", months, TODAY) == (11 * 150 + 140, 11 * 150)
    assert prepayments(data.conditions, "9999", months, TODAY) is None


def test_operating_costs_from_portal_forecast() -> None:
    fc = calculate(make_data(), BK, TODAY)
    assert fc is not None
    assert (fc.period_from, fc.period_to) == (date(2026, 1, 1), date(2026, 12, 31))
    assert fc.cost_basis_source == "prognose"
    assert fc.expected_costs == 1980.0  # keine Wasserpositionen → keine Verbrauchsanpassung
    assert fc.prepayments_total == 1790.0
    assert fc.result == 190.0  # Nachzahlung


def test_operating_costs_water_share_scaled() -> None:
    data = make_data()
    st = data.settlements["Betriebskosten"]
    st["KostenMvPrognose"] = 0  # → Kosten der letzten Abrechnung
    st["Nebenkostenschluessel"]["results"].append(
        {"Nksl": "2200", "NkslText": "Wasserversorgung/Entwässerung", "KostenNkslMv": 391.5})
    # Vorjahr aus der Langzeitstatistik: Aug–Okt 2025 doppelt so viel wie 2026
    data.archive = {
        "kaltwasser": {(2025, 8): 6.8, (2025, 9): 3.2, (2025, 10): 7.0},
        "warmwasser": {(2025, 8): 2.2, (2025, 9): 1.2, (2025, 10): 2.2},
    }
    fc = calculate(data, BK, TODAY)
    assert fc.cost_basis_source == "vorjahr" and fc.cost_basis == 1957.5
    assert fc.compared_months == 3 and fc.consumption_factor == pytest.approx(0.5)
    # 20 % Wasseranteil halbiert sich: 1957,5 − 0,2 × 1957,5 × 0,5
    assert fc.expected_costs == pytest.approx(1761.75)
    assert fc.result == pytest.approx(1761.75 - 1790)


def test_water_settlement_counts_to_operating_costs() -> None:
    data = make_data()
    data.settlements["Wasserkosten"] = {"KostenMv": 300.0, "AbrechnungBis": "2025-12-31"}
    assert calculate(data, BK, TODAY).expected_costs == 2280.0


def test_heating_costs_consumption_adjusted() -> None:
    data = make_data()
    data.settlements["Heiz- und Warmwasserkosten"] = _heating_settlement()
    data.archive = {
        "heizenergie": {(2025, 8): 0.0, (2025, 9): 0.0, (2025, 10): 145.0},
        "warmwasser_energie": {(2025, 8): 65.0, (2025, 9): 35.0, (2025, 10): 60.0},
    }
    fc = calculate(data, HK, TODAY)
    # 2026: 0+65, 0+35, 82+60 = 242 kWh; 2025: 65+35+205 = 305 kWh
    assert fc.consumption_factor == pytest.approx(242 / 305)
    expected = 1500 * (0.3 + 0.7 * 242 / 305)
    assert fc.expected_costs == pytest.approx(expected, abs=0.01)
    assert fc.prepayments_total == 12 * 120.0
    assert fc.result == pytest.approx(expected - 1440, abs=0.01)


def test_heating_without_comparison_uses_basis() -> None:
    data = make_data()
    data.settlements["Heizkosten"] = _heating_settlement(KostenMvPrognose=1600.0)
    fc = calculate(data, HK, TODAY)
    assert fc.consumption_factor is None and fc.compared_months == 0
    assert fc.expected_costs == 1600.0 and fc.result == 160.0


def test_no_settlement() -> None:
    assert calculate(make_data(), HK, TODAY) is None
