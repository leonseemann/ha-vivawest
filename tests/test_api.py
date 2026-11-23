"""Tests für den API-Client mit simulierten HTTP-Antworten (erfundene Daten)."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.vivawest.api import BASE_URL, VivawestAuthError, VivawestClient

METER = {
    "geraet": {"id": 1, "nr": "K-1", "raum": "Bad", "einheit": "CBM",
               "wohneinheit": {"gebaeude": {"strasse": "Musterweg"}}},
    "messwerte": [{"monat": 10, "jahr": 2026, "verbrauchswert": 4.2, "geraet": {"id": 1}}],
}


JSON = {"content-type": "application/json"}


def mock_portal(mock: AiohttpClientMocker, *, damages_status: int = 200) -> None:
    def get(path: str, body, status: int = 200) -> None:
        mock.get(BASE_URL + path, json=body, status=status, headers=JSON)

    mock.post(BASE_URL + "login", json={"sessionId": "s", "partnerId": "42"}, headers=JSON)
    mock.put(BASE_URL + "logout", json={}, headers=JSON)
    get("uvi/current", {
        "types": ["kaltwasser"], "messwerte": {"kaltwasser": {"verbrauchswert": 2.5}}})
    get("uvi/current/kaltwasser", {
        "messwerte": [{"monat": 10, "jahr": 2026, "verbrauchswert": 2.5}], "geraete": [METER]})
    get("contracts/conditions", [])
    get("additional-costs/settlements", [{
        "Type": "Betriebskosten", "BillingOverview2Periods": {"results": [
            {"Ident": "ALT", "AbrechnungBis": "2024-12-31"},
            {"Ident": "NEU", "AbrechnungBis": "2025-12-31"}]}}])
    get("additional-costs/details/NEU", {"settlement": {"AeErgebnis": 12.3}, "prepayments": []})
    get("postbox/unread", {"UnreadPostbox": 1, "UnreadArchiv": 0})
    get("damages/reports", {"results": [{"Schadensbeschreibung": "Tür klemmt"}]},
        damages_status)
    get("notification-badges", {"V1": {"count": 2, "badges": []}, "V2": {"count": 1}})


async def test_fetch_all(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_portal(aioclient_mock)
    client = VivawestClient(async_get_clientsession(hass), "a@b.de", "pw")
    data = await client.fetch_all()

    assert data.partner_id == "42"
    meter = data.meters["kaltwasser"][0]
    assert "wohneinheit" not in meter["geraet"]  # Adressdaten verworfen
    assert "geraet" not in meter["messwerte"][0]
    assert data.settlements == {"Betriebskosten": {"AeErgebnis": 12.3}}  # neueste Abrechnung
    assert data.unread_messages == 1
    assert data.notifications == 3
    assert data.open_damages == [{"Schadensbeschreibung": "Tür klemmt"}]


async def test_optional_endpoint_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_portal(aioclient_mock, damages_status=500)
    client = VivawestClient(async_get_clientsession(hass), "a@b.de", "pw")
    data = await client.fetch_all()
    assert data.open_damages is None
    assert data.unread_messages == 1


async def test_auth_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(BASE_URL + "login", status=403, json={"message": "falsch"}, headers=JSON)
    client = VivawestClient(async_get_clientsession(hass), "a@b.de", "pw")
    with pytest.raises(VivawestAuthError):
        await client.fetch_all()
