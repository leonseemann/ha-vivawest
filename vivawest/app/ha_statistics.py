"""
Rückwirkender Import der Monatsverbräuche in die Home-Assistant-Langzeitstatistik.

Vivawest liefert nur einen Wert pro Monat (nach Monatsende). Dieser wird gleichmäßig
auf alle Stunden des Monats verteilt und als externe Statistik
(``vivawest:<name>``) über die WebSocket-API ``recorder/import_statistics``
geschrieben. Die Statistiken sind im Energie-Dashboard auswählbar.

Damit die kumulierte Summe stetig bleibt, merkt sich das Add-on pro Statistik
die importierten Monate samt Endsumme in einer JSON-Datei (``/data/state.json``).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import websocket  # websocket-client

from vivawest_api import month_values

log = logging.getLogger("vivawest")

STATISTIC_SOURCE = "vivawest"


@dataclass(frozen=True)
class StatDef:
    key: str          # Schlüssel in state.json
    name: str
    unit: str
    unit_class: str   # HA-Einheitenklasse ("volume" / "energy")
    uvi_type: str     # Verbrauchsart der API
    field: str        # Feld im Monatseintrag

    @property
    def statistic_id(self) -> str:
        return f"{STATISTIC_SOURCE}:{self.key}"


STATISTICS = [
    StatDef("kaltwasser", "Vivawest Kaltwasser", "m³", "volume", "kaltwasser", "verbrauchswert"),
    StatDef("warmwasser", "Vivawest Warmwasser", "m³", "volume", "warmwasser", "verbrauchswert"),
    StatDef("warmwasser_energie", "Vivawest Warmwasser Energie", "kWh", "energy", "warmwasser", "kwh"),
    StatDef("heizenergie", "Vivawest Heizenergie", "kWh", "energy", "heizenergie", "kwh"),
]


# ---------------------------------------------------------------------------
# Verteilung
# ---------------------------------------------------------------------------
def month_hours(year: int, month: int, tz: ZoneInfo) -> list[datetime]:
    """Alle Stundenanfänge eines Monats in lokaler Zeit (berücksichtigt Sommer-/Winterzeit)."""
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=tz)
    cur, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    hours = []
    while cur < end_utc:
        hours.append(cur)
        cur += timedelta(hours=1)
    return hours


def distribute(year: int, month: int, value: float, start_sum: float,
               tz: ZoneInfo) -> tuple[list[dict], float]:
    """Verteilt ``value`` gleichmäßig auf die Stunden des Monats.

    Liefert die Statistik-Zeilen und die Summe am Monatsende.
    """
    hours = month_hours(year, month, tz)
    per_hour = value / len(hours)
    rows = []
    total = start_sum
    for i, hour in enumerate(hours, start=1):
        total = start_sum + per_hour * i
        rows.append({"start": hour.isoformat(), "state": round(total, 6), "sum": round(total, 6)})
    return rows, start_sum + value


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def plan_import(stored: dict[str, dict], values: dict[tuple[int, int], float]
                ) -> list[tuple[int, int, float, float]]:
    """Bestimmt, welche Monate (neu) importiert werden müssen.

    ``stored``: {"YYYY-MM": {"value": v, "sum_end": s}} aus state.json
    ``values``: {(jahr, monat): wert} aus der API
    Rückgabe: [(jahr, monat, wert, startsumme), ...] aufsteigend sortiert.
    Ab dem ersten neuen/geänderten Monat werden alle späteren Monate neu
    berechnet, damit die Summe stetig bleibt.
    """
    merged = {k: v["value"] for k, v in stored.items()}
    first_changed: Optional[str] = None
    for (y, m), value in sorted(values.items()):
        key = _month_key(y, m)
        old = stored.get(key)
        if old is None or abs(old["value"] - value) > 1e-9:
            first_changed = key if first_changed is None else min(first_changed, key)
        merged[key] = value
    if first_changed is None:
        return []

    keys = sorted(merged)
    idx = keys.index(first_changed)
    start_sum = stored[keys[idx - 1]]["sum_end"] if idx > 0 and keys[idx - 1] in stored else 0.0
    plan = []
    for key in keys[idx:]:
        y, m = map(int, key.split("-"))
        plan.append((y, m, merged[key], start_sum))
        start_sum += merged[key]
    return plan


# ---------------------------------------------------------------------------
# Zustand
# ---------------------------------------------------------------------------
def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("Statusdatei %s nicht lesbar (%s) – starte neu.", path, e)
        return {}


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Home Assistant WebSocket
# ---------------------------------------------------------------------------
class HomeAssistantError(Exception):
    pass


class HomeAssistantWS:
    def __init__(self, url: str, token: str, timeout: int = 30) -> None:
        self._url = url
        self._token = token
        self._timeout = timeout
        self._ws: Optional[websocket.WebSocket] = None
        self._msg_id = 0

    def __enter__(self) -> "HomeAssistantWS":
        self._ws = websocket.create_connection(self._url, timeout=self._timeout)
        hello = json.loads(self._ws.recv())
        if hello.get("type") != "auth_required":
            raise HomeAssistantError(f"Unerwartete Begrüßung: {hello.get('type')}")
        self._ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        auth = json.loads(self._ws.recv())
        if auth.get("type") != "auth_ok":
            raise HomeAssistantError(f"Authentifizierung fehlgeschlagen: {auth.get('message')}")
        return self

    def __exit__(self, *exc) -> None:
        if self._ws:
            self._ws.close()

    def call(self, msg_type: str, **payload) -> dict:
        self._msg_id += 1
        msg_id = self._msg_id
        self._ws.send(json.dumps({"id": msg_id, "type": msg_type, **payload}))
        while True:
            msg = json.loads(self._ws.recv())
            if msg.get("id") == msg_id and msg.get("type") == "result":
                if not msg.get("success"):
                    err = msg.get("error") or {}
                    raise HomeAssistantError(f"{msg_type}: {err.get('code')} – {err.get('message')}")
                return msg.get("result") or {}

    def time_zone(self) -> str:
        return self.call("get_config").get("time_zone") or "Europe/Berlin"

    def import_statistics(self, stat: StatDef, rows: list[dict]) -> None:
        base = {
            "statistic_id": stat.statistic_id,
            "source": STATISTIC_SOURCE,
            "name": stat.name,
            "unit_of_measurement": stat.unit,
            "has_sum": True,
        }
        try:
            # Aktuelles Format (HA ≥ 2025.x): mean_type + unit_class
            self.call("recorder/import_statistics",
                      metadata={**base, "mean_type": 0, "unit_class": stat.unit_class}, stats=rows)
        except HomeAssistantError as e:
            log.debug("Neues Metadatenformat abgelehnt (%s) – versuche altes Format.", e)
            self.call("recorder/import_statistics",
                      metadata={**base, "has_mean": False}, stats=rows)


def ha_websocket_target() -> Optional[tuple[str, str]]:
    """URL und Token für die HA-WebSocket-API, oder None wenn nicht verfügbar.

    Im Add-on: Supervisor-Proxy mit SUPERVISOR_TOKEN (benötigt homeassistant_api: true).
    Lokal: HA_URL (z. B. http://homeassistant.local:8123) + HA_TOKEN (Long-Lived Token).
    """
    if os.environ.get("SUPERVISOR_TOKEN"):
        return "ws://supervisor/core/websocket", os.environ["SUPERVISOR_TOKEN"]
    url, token = os.environ.get("HA_URL"), os.environ.get("HA_TOKEN")
    if url and token:
        ws_url = url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
        return ws_url + "/api/websocket", token
    return None


# ---------------------------------------------------------------------------
# Gesamtablauf
# ---------------------------------------------------------------------------
def import_all(histories: dict[str, list[dict]], state_path: str) -> None:
    """Importiert alle neuen/geänderten Monate.

    ``histories``: {uvi_type: Monatseinträge aus VivawestClient.get_history()}
    """
    target = ha_websocket_target()
    if target is None:
        log.warning("Kein Zugriff auf die Home-Assistant-API – Statistik-Import übersprungen.")
        return

    state = load_state(state_path)
    with HomeAssistantWS(*target) as ha:
        tz = ZoneInfo(ha.time_zone())
        for stat in STATISTICS:
            values = month_values(histories.get(stat.uvi_type, []), stat.field)
            if not values:
                continue
            stored = state.setdefault(stat.key, {})
            plan = plan_import(stored, values)
            if not plan:
                log.info("Statistik %s ist aktuell.", stat.statistic_id)
                continue
            for year, month, value, start_sum in plan:
                rows, sum_end = distribute(year, month, value, start_sum, tz)
                ha.import_statistics(stat, rows)
                stored[_month_key(year, month)] = {"value": value, "sum_end": sum_end}
                save_state(state_path, state)
                log.info("Statistik %s: %02d/%d = %s %s auf %d Stunden verteilt.",
                         stat.statistic_id, month, year, value, stat.unit, len(rows))
