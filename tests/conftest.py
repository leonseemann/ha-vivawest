"""Gemeinsame Fixtures. Die Testdaten sind erfunden, aber wie die echten API-Antworten aufgebaut."""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.vivawest.api import VivawestData

EMAIL = "mieter@example.com"
PASSWORD = "geheim"
PARTNER_ID = "1000000001"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Recorder (Abhängigkeit der Integration) und Custom Integrations in allen Tests laden."""
    return


def _month(jahr: int, monat: int, wert: float, kwh: float | None = None) -> dict:
    entry = {"jahr": jahr, "monat": monat, "verbrauchswert": wert,
             "ablesedatum": f"{jahr + (monat == 12)}-{monat % 12 + 1:02d}-02T00:00:00+01:00"}
    if kwh is not None:
        entry["kwh"] = kwh
    return entry


def make_data() -> VivawestData:
    kalt = [_month(2026, m, v) for m, v in [(10, 3.5), (9, 1.6), (8, 3.4)]]
    warm = [_month(2026, m, v, k) for m, v, k in [(10, 1.1, 60.0), (9, 0.6, 35.0), (8, 1.1, 65.0)]]
    heiz = [_month(2026, m, v, k) for m, v, k in [(10, 80.0, 82.0), (9, 0.0, 0.0), (8, 0.0, 0.0)]]
    current = {
        "kaltwasser": kalt[0],
        "warmwasser": warm[0] | {"differenz_liegenschaft_qm_prozent": -13.4},
        "heizenergie": heiz[0] | {"differenz_liegenschaft_qm_prozent": 4.59},
    }

    def period(von, bis, gueltig, items):
        return {"GueltigVon": von, "GueltigBis": bis, "Gueltig": gueltig,
                "GrossAmountdec": f"{sum(a for _, _, a in items):.6f}",
                "ConditionItems": {"results": [
                    {"Condtype": c, "Xcondtypel": n, "GrossAmountdec": f"{a:.6f}"} for c, n, a in items]}}

    conditions = [
        period("2026-05-01", "2026-11-30", True,
               [("1000", "Grundmiete Wohnung", 500.0), ("3760", "TV-Versorgung", 10.0),
                ("4000", "Vorauszahlung Betriebskosten", 150.0), ("4010", "Vorauszahlung Heizkosten", 120.0)]),
        period("2026-12-01", "9999-12-31", False,
               [("1000", "Grundmiete Wohnung", 500.0), ("4000", "Vorauszahlung Betriebskosten", 140.0),
                ("4010", "Vorauszahlung Heizkosten", 120.0)]),
    ]
    return VivawestData(partner_id=PARTNER_ID, current=current, heating_unit="ve",
                        history={"kaltwasser": kalt, "warmwasser": warm, "heizenergie": heiz},
                        conditions=conditions)


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Ersetzt den API-Client in Coordinator und Config Flow."""
    with (
        patch("custom_components.vivawest.coordinator.VivawestClient", autospec=True) as coord_cls,
        patch("custom_components.vivawest.config_flow.VivawestClient", new=coord_cls),
    ):
        client = coord_cls.return_value
        client.login.return_value = PARTNER_ID
        client.fetch_all.return_value = make_data()
        yield client
