"""Konstanten der Vivawest-Integration."""
from __future__ import annotations

from datetime import timedelta
import logging

DOMAIN = "vivawest"
LOGGER = logging.getLogger(__package__)

# Vivawest veröffentlicht die Werte nur monatlich – häufiger abzufragen bringt nichts.
UPDATE_INTERVAL = timedelta(hours=12)
