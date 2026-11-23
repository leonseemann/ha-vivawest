"""Einrichtung per UI: Zugangsdaten zum Kundenportal."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import VivawestAuthError, VivawestClient, VivawestError
from .const import DOMAIN, LOGGER

USER_SCHEMA = vol.Schema({vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str})


class VivawestConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, email: str, password: str) -> tuple[str | None, dict[str, str]]:
        """Prüft die Zugangsdaten. Rückgabe: (partner_id, errors)."""
        client = VivawestClient(async_create_clientsession(self.hass), email, password)
        try:
            partner_id = await client.login()
        except VivawestAuthError:
            return None, {"base": "invalid_auth"}
        except VivawestError:
            return None, {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unerwarteter Fehler bei der Anmeldung")
            return None, {"base": "unknown"}
        finally:
            await client.logout()
        return partner_id or email.lower(), {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id, errors = await self._validate(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if not errors:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=user_input[CONF_EMAIL], data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id, errors = await self._validate(entry.data[CONF_EMAIL], user_input[CONF_PASSWORD])
            if not errors:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )
