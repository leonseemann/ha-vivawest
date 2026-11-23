"""Tests für Setup, Sensoren und Fehlerbehandlung."""
from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from custom_components.vivawest.api import VivawestAuthError, VivawestError
from custom_components.vivawest.const import DOMAIN, UPDATE_INTERVAL

from .conftest import EMAIL, PARTNER_ID, PASSWORD


def get_state(hass: HomeAssistant, key: str) -> State:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{PARTNER_ID}_{key}")
    assert entity_id, key
    return hass.states.get(entity_id)


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PARTNER_ID, title=EMAIL,
                            data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD})
    entry.add_to_hass(hass)
    return entry


@pytest.fixture(autouse=True)
def no_statistics(request):
    """Statistik-Import in Sensortests abschalten (eigener Test mit Recorder)."""
    from unittest.mock import patch
    with patch("custom_components.vivawest.coordinator.async_import_statistics") as m:
        yield m


@pytest.mark.freeze_time("2026-11-23 10:00:00+01:00")
async def test_sensors(hass: HomeAssistant, entry, mock_client) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    state = get_state(hass, "kaltwasser")
    assert state.state == "3.5"
    assert state.attributes["unit_of_measurement"] == "m³"
    assert state.attributes["monat"] == 10
    assert "state_class" not in state.attributes

    assert get_state(hass, "heizenergie").attributes["vergleich_liegenschaft_prozent"] == 4.6
    assert get_state(hass, "heizung_verbrauchseinheiten").state == "80.0"

    total = get_state(hass, "miete_gesamt")
    assert float(total.state) == 780.0
    assert total.attributes["naechster_betrag"] == 760.0
    assert total.attributes["naechste_aenderung"] == "2026-12-01"
    assert total.attributes["positionen"]["TV-Versorgung"] == 10.0

    bk = get_state(hass, "vorauszahlung_betriebskosten")
    assert float(bk.state) == 150.0 and bk.attributes["naechster_betrag"] == 140.0
    assert "naechster_betrag" not in get_state(hass, "vorauszahlung_heizkosten").attributes

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_retry_on_connection_error(hass: HomeAssistant, entry, mock_client) -> None:
    mock_client.fetch_all.side_effect = VivawestError("down")
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_error_starts_reauth(hass: HomeAssistant, entry, mock_client) -> None:
    mock_client.fetch_all.side_effect = VivawestAuthError("falsch")
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_unavailable_after_failed_update(
    hass: HomeAssistant, entry, mock_client, freezer: FrozenDateTimeFactory
) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_client.fetch_all.side_effect = VivawestError("down")
    freezer.tick(UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert get_state(hass, "kaltwasser").state == STATE_UNAVAILABLE


async def test_diagnostics_redacts(hass: HomeAssistant, entry, mock_client) -> None:
    from custom_components.vivawest.diagnostics import async_get_config_entry_diagnostics
    await hass.config_entries.async_setup(entry.entry_id)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"][CONF_EMAIL] == "**REDACTED**"
    assert diag["entry"][CONF_PASSWORD] == "**REDACTED**"
