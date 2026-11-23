"""Konstanten der Vivawest-Integration."""
from __future__ import annotations

from datetime import timedelta
import logging

DOMAIN = "vivawest"
LOGGER = logging.getLogger(__package__)

# Vivawest veröffentlicht die Werte nur monatlich – häufiger abzufragen bringt nichts.
UPDATE_INTERVAL = timedelta(hours=12)

# Optionen: Preise für die Kostenstatistiken (Energie-Dashboard)
CONF_PRICE_WATER = "preis_wasser"  # €/m³, Kalt- und Warmwasser
CONF_PRICE_HOT_WATER_ENERGY = "preis_warmwasser_energie"  # €/kWh
CONF_PRICE_HEATING = "preis_heizenergie"  # €/kWh
