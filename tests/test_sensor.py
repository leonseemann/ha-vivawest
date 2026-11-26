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

from .conftest import EMAIL, PARTNER_ID, PASSWORD, make_data


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

    assert float(get_state(hass, "tv_versorgung").state) == 10.0

    prognose = get_state(hass, "prognose_betriebskosten")
    assert float(prognose.state) == 190.0
    assert prognose.attributes["ergebnis"] == "Nachzahlung"
    assert prognose.attributes["zeitraum_von"] == "2026-01-01"
    assert prognose.attributes["vorauszahlungen_bisher"] == 1650.0
    assert prognose.attributes["kostendeckende_vorauszahlung"] == 165.0
    assert get_state(hass, "prognose_heizkosten").state == STATE_UNAVAILABLE
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.freeze_time("2026-11-23 10:00:00+01:00")
async def test_meter_sensors(hass: HomeAssistant, entry, mock_client) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    bad = get_state(hass, "zaehler_101")
    assert bad.state == "42.6"  # jüngster Monat
    assert bad.attributes["unit_of_measurement"] == "m³"
    assert bad.attributes["state_class"] == "total_increasing"
    assert bad.attributes["monatsverbrauch"] == 2.5
    assert bad.attributes["raum"] == "Bad" and bad.attributes["monat"] == 10
    assert bad.name.endswith("Bad")

    assert get_state(hass, "zaehler_201_energie").state == "870.0"
    assert get_state(hass, "zaehler_201_energie").attributes["unit_of_measurement"] == "kWh"
    with pytest.raises(AssertionError):
        get_state(hass, "zaehler_101_energie")  # Kaltwasser ohne kWh

    # Zwei Heizkostenverteiler im selben Raum → Zählernummer im Namen
    hkv = get_state(hass, "zaehler_301")
    assert hkv.state == "50.0" and hkv.attributes["unit_of_measurement"] == "VE"
    assert hkv.name.endswith("Wohnzimmer H-1")
    assert hkv.attributes["umrechnungsfaktor"] == 2.1


@pytest.mark.freeze_time("2026-11-23 10:00:00+01:00")
async def test_settlement_damage_postbox_sensors(hass: HomeAssistant, entry, mock_client) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    bk = get_state(hass, "abrechnung_betriebskosten")
    assert float(bk.state) == -42.5
    assert bk.attributes["ergebnis"] == "Guthaben"
    assert bk.attributes["zeitraum_bis"] == "2025-12-31"
    assert bk.attributes["neue_vorauszahlung"] == 160.0
    assert bk.attributes["positionen"] == {"Gebäudeversicherung": 247.76}
    assert "faellig_am" not in bk.attributes  # Platzhalterdatum

    assert get_state(hass, "ungelesene_nachrichten").state == "2"
    assert get_state(hass, "portal_neuigkeiten").state == "3"
    damages = get_state(hass, "offene_schadensmeldungen")
    assert damages.state == "2"
    assert damages.attributes["meldungen"][0]["termin"].startswith("2026-11-27T07:00")
    assert get_state(hass, "naechster_handwerkertermin").state == "2026-11-27T06:00:00+00:00"


@pytest.mark.freeze_time("2026-11-23 10:00:00+01:00")
async def test_optional_data_missing(hass: HomeAssistant, entry, mock_client) -> None:
    data = mock_client.fetch_all.return_value
    data.open_damages = None
    data.unread_messages = None
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert get_state(hass, "offene_schadensmeldungen").state == STATE_UNAVAILABLE
    assert get_state(hass, "ungelesene_nachrichten").state == STATE_UNAVAILABLE
    # Kein Termin → unbekannt, nicht unavailable
    assert get_state(hass, "naechster_handwerkertermin").state == "unknown"
    assert get_state(hass, "kaltwasser").state == "3.5"


async def test_new_meter_added_on_update(
    hass: HomeAssistant, entry, mock_client, freezer: FrozenDateTimeFactory
) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = make_data()
    data.meters["kaltwasser"].append(
        {"geraet": {"id": 103, "nr": "K-3", "raum": "Keller", "einheit": "CBM"},
         "messwerte": [{"monat": 10, "jahr": 2026, "verbrauchswert": 1.0}]})
    mock_client.fetch_all.return_value = data
    freezer.tick(UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert get_state(hass, "zaehler_103").state == "1.0"


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


@pytest.mark.freeze_time("2026-11-23 10:00:00+01:00")
async def test_forecast_with_prices(hass: HomeAssistant, entry, mock_client) -> None:
    hass.config_entries.async_update_entry(entry, options={"preis_wasser": 5.0})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = get_state(hass, "prognose_betriebskosten")
    assert state.attributes["methode"] == "verbrauch"
    assert state.attributes["monate_mit_verbrauchswerten"] == 3
    # Keine Wasserposition in der Testabrechnung → Wasser kommt zur Vivawest-Prognose hinzu
    assert float(state.state) == pytest.approx(1980 + 11.3 * 4 * 5 - 1790)
