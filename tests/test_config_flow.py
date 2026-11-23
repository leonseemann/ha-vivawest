"""Tests für den Config Flow."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.vivawest.api import VivawestAuthError, VivawestError
from custom_components.vivawest.const import DOMAIN

from .conftest import EMAIL, PARTNER_ID, PASSWORD


async def test_user_flow(hass: HomeAssistant, mock_client) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["result"].unique_id == PARTNER_ID
    mock_client.logout.assert_awaited()


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [(VivawestAuthError("x"), "invalid_auth"), (VivawestError("x"), "cannot_connect"),
     (RuntimeError("x"), "unknown")],
)
async def test_user_flow_errors(hass: HomeAssistant, mock_client, side_effect, error) -> None:
    mock_client.login.side_effect = side_effect
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # Danach klappt es
    mock_client.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(hass: HomeAssistant, mock_client) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=PARTNER_ID, data={}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, mock_client) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=PARTNER_ID,
                            data={CONF_EMAIL: EMAIL, CONF_PASSWORD: "alt"})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "neu"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "neu"


async def test_reauth_wrong_account(hass: HomeAssistant, mock_client) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="anderer",
                            data={CONF_EMAIL: EMAIL, CONF_PASSWORD: "alt"})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "neu"})
    assert result["reason"] == "wrong_account"
