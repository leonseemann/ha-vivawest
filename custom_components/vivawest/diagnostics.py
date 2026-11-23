"""Diagnose-Download (persönliche Daten geschwärzt)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import VivawestConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, "partner_id", "Recnnr", "IntrenoCn", "ObjnrCn", "virtual_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VivawestConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data.data
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "current": data.current,
            "heating_unit": data.heating_unit,
            "history_months": {k: len(v) for k, v in data.history.items()},
            "conditions": data.conditions,
        },
        TO_REDACT,
    )
