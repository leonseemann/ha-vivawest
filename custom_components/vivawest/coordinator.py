"""DataUpdateCoordinator der Vivawest-Integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VivawestAuthError, VivawestClient, VivawestData, VivawestError
from .const import DOMAIN, LOGGER, UPDATE_INTERVAL
from .statistics import async_import_statistics, async_read_monthly_values

type VivawestConfigEntry = ConfigEntry[VivawestCoordinator]


class VivawestCoordinator(DataUpdateCoordinator[VivawestData]):
    """Ruft alle 12 Stunden Verbräuche und Mietkonditionen ab."""

    config_entry: VivawestConfigEntry

    def __init__(self, hass: HomeAssistant, entry: VivawestConfigEntry) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = VivawestClient(
            async_create_clientsession(hass),
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
        )

    async def _async_update_data(self) -> VivawestData:
        try:
            data = await self.client.fetch_all()
        except VivawestAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except VivawestError as err:
            raise UpdateFailed(str(err)) from err

        try:
            await async_import_statistics(self.hass, data.history, self.config_entry.options)
        except Exception:  # noqa: BLE001 – Statistik darf die Sensoren nicht blockieren
            LOGGER.exception("Statistik-Import fehlgeschlagen")
        try:
            data.archive = await async_read_monthly_values(self.hass)
        except Exception:  # noqa: BLE001 – nur für den Vorjahresvergleich der Prognose
            LOGGER.exception("Lesen der Langzeitstatistik fehlgeschlagen")
        return data
