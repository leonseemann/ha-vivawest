"""
Vivawest Home Assistant Add-on
Liest Heizungs-, Kaltwasser- und Warmwasser-Verbräuche aus dem Vivawest
Kundenportal, veröffentlicht den letzten Monatswert als HA-Sensoren via
MQTT Discovery und importiert die Monatsverbräuche – gleichmäßig auf die
Stunden des jeweiligen Monats verteilt – rückwirkend in die Langzeitstatistik.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import date
from typing import Optional

import paho.mqtt.client as mqtt
import requests

from ha_statistics import import_all
from vivawest_api import VivawestClient, VivawestError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("vivawest")

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
MQTT_DISCOVERY_PREFIX = "homeassistant"
MQTT_AVAILABILITY_TOPIC = "vivawest/availability"
MIN_INTERVAL_HOURS = 1
REQUEST_TIMEOUT = 15
MQTT_CONNECT_RETRIES = 5
MQTT_CONNECT_RETRY_DELAY = 10
STATE_FILE = os.environ.get(
    "STATE_FILE", "/data/state.json" if os.path.isdir("/data") else "state.json"
)


@dataclass(frozen=True)
class SensorDef:
    sensor_id: str               # Teil der unique_id – nicht umbenennen!
    name: str
    unit: str
    source: str                  # Verbrauchsart der API bzw. "kosten"
    field: str                   # Feld im Messwert bzw. Konditionsart
    device_class: Optional[str]
    icon: str


SENSORS = [
    SensorDef("kaltwasser_verbrauch", "Vivawest Kaltwasser Verbrauch", "m³",
              "kaltwasser", "verbrauchswert", "water", "mdi:water"),
    SensorDef("warmwasser_verbrauch", "Vivawest Warmwasser Verbrauch", "m³",
              "warmwasser", "verbrauchswert", "water", "mdi:water-thermometer"),
    SensorDef("warmwasser_kwh", "Vivawest Warmwasser Energie", "kWh",
              "warmwasser", "kwh", "energy", "mdi:lightning-bolt"),
    SensorDef("heizenergie_kwh", "Vivawest Heizenergie", "kWh",
              "heizenergie", "kwh", "energy", "mdi:radiator"),
    SensorDef("heizenergie_ve", "Vivawest Heizung Verbrauchseinheiten", "VE",
              "heizenergie", "verbrauchswert", None, "mdi:radiator"),
]

# Mietkonditionen (Monatsbeträge). field = Konditionsart (Condtype) des Portals,
# "gesamt" = Summe aller Positionen.
COST_SENSORS = [
    SensorDef("miete_gesamt", "Vivawest Miete gesamt", "EUR",
              "kosten", "gesamt", "monetary", "mdi:home-currency-eur"),
    SensorDef("grundmiete", "Vivawest Grundmiete", "EUR",
              "kosten", "1000", "monetary", "mdi:home-currency-eur"),
    SensorDef("vorauszahlung_betriebskosten", "Vivawest Vorauszahlung Betriebskosten", "EUR",
              "kosten", "4000", "monetary", "mdi:cash-multiple"),
    SensorDef("vorauszahlung_heizkosten", "Vivawest Vorauszahlung Heizkosten", "EUR",
              "kosten", "4010", "monetary", "mdi:radiator"),
]


# ---------------------------------------------------------------------------
# Konfiguration aus /data/options.json (Home Assistant Add-on Standard)
# ---------------------------------------------------------------------------
def load_config() -> dict:
    options_path = "/data/options.json"
    if not os.path.exists(options_path):
        log.warning("options.json nicht gefunden – nutze Umgebungsvariablen.")
        return {
            "login": os.environ.get("VIVAWEST_LOGIN", ""),
            "password": os.environ.get("VIVAWEST_PASSWORD", ""),
            "mqtt_host": os.environ.get("MQTT_HOST", "core-mosquitto"),
            "mqtt_port": int(os.environ.get("MQTT_PORT", "1883")),
            "mqtt_user": os.environ.get("MQTT_USER", ""),
            "mqtt_password": os.environ.get("MQTT_PASSWORD", ""),
            "poll_interval_hours": int(os.environ.get("POLL_INTERVAL_HOURS", "1")),
            "import_statistics": os.environ.get("IMPORT_STATISTICS", "true").lower() == "true",
        }
    with open(options_path) as f:
        return json.load(f)


def get_poll_interval(cfg: dict) -> int:
    """Liest das Intervall aus der Config, erzwingt Minimum 1 Stunde."""
    hours = int(cfg.get("poll_interval_hours", MIN_INTERVAL_HOURS))
    if hours < MIN_INTERVAL_HOURS:
        log.warning(
            "Abfrageintervall %d h ist unter dem Minimum (%d h) – setze auf %d h.",
            hours, MIN_INTERVAL_HOURS, MIN_INTERVAL_HOURS,
        )
        hours = MIN_INTERVAL_HOURS
    return hours * 3600


# ---------------------------------------------------------------------------
# MQTT Helpers
# ---------------------------------------------------------------------------
def state_topic(sensor_id: str) -> str:
    return f"vivawest/sensor/{sensor_id}/state"


def publish_discovery(client: mqtt.Client, sensor: SensorDef) -> None:
    topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/vivawest_{sensor.sensor_id}/config"
    payload = {
        "name": sensor.name,
        "unique_id": f"vivawest_{sensor.sensor_id}",
        "state_topic": state_topic(sensor.sensor_id),
        "unit_of_measurement": sensor.unit,
        "value_template": "{{ value_json.value }}",
        "json_attributes_topic": state_topic(sensor.sensor_id),
        "availability_topic": MQTT_AVAILABILITY_TOPIC,
        "icon": sensor.icon,
        "device": {
            "identifiers": ["vivawest_addon"],
            "name": "Vivawest",
            "model": "Kundenportal Add-on",
            "manufacturer": "Vivawest",
        },
    }
    # Bewusst ohne state_class: Der Wert ist der Verbrauch des letzten Monats,
    # kein Zählerstand. Die Langzeitstatistik kommt aus dem Statistik-Import.
    if sensor.device_class:
        payload["device_class"] = sensor.device_class
    client.publish(topic, json.dumps(payload), retain=True)
    log.info("Discovery gesendet: %s", sensor.name)


def register_sensors(client: mqtt.Client) -> None:
    for sensor in SENSORS + COST_SENSORS:
        publish_discovery(client, sensor)


def publish_state(client: mqtt.Client, sensor_id: str, value, attributes: dict) -> None:
    payload = {"value": value, **attributes}
    client.publish(state_topic(sensor_id), json.dumps(payload), retain=True)
    log.info("State veröffentlicht: %s = %s", sensor_id, value)


def process_and_publish(client: mqtt.Client, current: dict) -> None:
    """Veröffentlicht den letzten Monatswert jeder Verbrauchsart."""
    messwerte = current.get("messwerte") or {}
    if not messwerte:
        log.warning("Keine 'messwerte' in der API-Antwort (Felder: %s).", sorted(current))
        return

    for sensor in SENSORS:
        entry = messwerte.get(sensor.source)
        if not entry or entry.get(sensor.field) is None:
            log.debug("Keine Daten für %s.", sensor.sensor_id)
            continue
        attributes = {
            "ablesedatum": entry.get("ablesedatum"),
            "monat": entry.get("monat"),
            "jahr": entry.get("jahr"),
        }
        if isinstance(entry.get("differenz_liegenschaft_qm_prozent"), (int, float)):
            attributes["vergleich_liegenschaft_prozent"] = round(entry["differenz_liegenschaft_qm_prozent"], 1)
        publish_state(client, sensor.sensor_id, entry[sensor.field], attributes)


def _amount(value) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _period_amounts(period: dict) -> dict[str, float]:
    """{Konditionsart: Betrag} eines Zeitraums, plus "gesamt"."""
    amounts = {"gesamt": _amount(period.get("GrossAmountdec"))}
    for item in (period.get("ConditionItems") or {}).get("results") or []:
        if item.get("Condtype"):
            amounts[item["Condtype"]] = _amount(item.get("GrossAmountdec"))
    return amounts


def _period_items(period: dict) -> dict[str, float]:
    """{Bezeichnung: Betrag} aller Positionen eines Zeitraums."""
    return {
        item.get("Xcondtypel") or item.get("Condtype"): _amount(item.get("GrossAmountdec"))
        for item in (period.get("ConditionItems") or {}).get("results") or []
    }


def select_periods(periods: list[dict], today: date) -> tuple[Optional[dict], list[dict]]:
    """Liefert den aktuell gültigen Zeitraum und die zukünftigen (aufsteigend)."""
    ordered = sorted(periods, key=lambda p: p.get("GueltigVon") or "")
    today_s = today.isoformat()
    current = next((p for p in ordered if p.get("Gueltig")), None)
    if current is None:
        current = next((p for p in reversed(ordered)
                        if (p.get("GueltigVon") or "") <= today_s <= (p.get("GueltigBis") or "9999")), None)
    if current is None:
        return None, []
    future = [p for p in ordered if (p.get("GueltigVon") or "") > (current.get("GueltigVon") or "")]
    return current, future


def process_conditions(client: mqtt.Client, periods: list[dict], today: Optional[date] = None) -> None:
    """Veröffentlicht die aktuellen Monatsbeträge samt nächster geplanter Änderung."""
    current, future = select_periods(periods, today or date.today())
    if current is None:
        log.warning("Keine gültigen Mietkonditionen gefunden.")
        return

    now = _period_amounts(current)
    for sensor in COST_SENSORS:
        if sensor.field not in now:
            log.debug("Keine Kondition %s vorhanden.", sensor.field)
            continue
        attributes = {
            "gueltig_von": current.get("GueltigVon"),
            "gueltig_bis": current.get("GueltigBis"),
        }
        if sensor.field == "gesamt":
            attributes["positionen"] = _period_items(current)
        # Erste zukünftige Änderung dieses Betrags (fehlende Position = 0 €)
        for period in future:
            amount = _period_amounts(period).get(sensor.field, 0.0)
            if amount != now[sensor.field]:
                attributes["naechster_betrag"] = amount
                attributes["naechste_aenderung"] = period.get("GueltigVon")
                break
        publish_state(client, sensor.sensor_id, now[sensor.field], attributes)


# ---------------------------------------------------------------------------
# MQTT Client aufbauen
# ---------------------------------------------------------------------------
def build_mqtt_client(cfg: dict) -> mqtt.Client:
    mqtt_client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="vivawest_addon",
    )

    if cfg.get("mqtt_user"):
        mqtt_client.username_pw_set(cfg["mqtt_user"], cfg.get("mqtt_password", ""))

    # Last Will and Testament – HA zeigt "Nicht verfügbar" bei unerwartetem Absturz
    mqtt_client.will_set(MQTT_AVAILABILITY_TOPIC, payload="offline", retain=True)

    def on_connect(client, userdata, flags, reason_code, properties):
        if not reason_code.is_failure:
            log.info("MQTT verbunden.")
            client.publish(MQTT_AVAILABILITY_TOPIC, "online", retain=True)
        else:
            log.error("MQTT Verbindung fehlgeschlagen: %s", reason_code)

    def on_disconnect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning(
                "MQTT unerwartet getrennt (%s) – automatische Wiederverbindung aktiv.", reason_code
            )

    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.reconnect_delay_set(min_delay=5, max_delay=60)

    return mqtt_client


# ---------------------------------------------------------------------------
# Abfrage-Zyklus
# ---------------------------------------------------------------------------
def run_cycle(mqtt_client: mqtt.Client, cfg: dict) -> None:
    with VivawestClient(cfg["login"], cfg["password"], timeout=REQUEST_TIMEOUT) as api:
        current = api.get_current()
        histories = {}
        if cfg.get("import_statistics", True):
            for uvi_type in current.get("types") or []:
                histories[uvi_type] = api.get_history(uvi_type)
        try:
            conditions = api.get_conditions()
        except (VivawestError, requests.RequestException) as e:
            log.warning("Mietkonditionen nicht abrufbar: %s", e)
            conditions = []

    process_and_publish(mqtt_client, current)
    if conditions:
        process_conditions(mqtt_client, conditions)

    if histories:
        try:
            import_all(histories, STATE_FILE)
        except Exception as e:
            log.exception("Statistik-Import fehlgeschlagen: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()

    if not cfg.get("login") or not cfg.get("password"):
        log.error("Login und/oder Passwort fehlen in der Konfiguration!")
        raise SystemExit(1)

    poll_interval_sec = get_poll_interval(cfg)
    log.info("Abfrageintervall: %d Stunde(n).", poll_interval_sec // 3600)

    mqtt_client = build_mqtt_client(cfg)

    host = cfg.get("mqtt_host", "core-mosquitto")
    port = int(cfg.get("mqtt_port", 1883))
    log.info("Verbinde mit MQTT-Broker %s:%d...", host, port)

    for attempt in range(1, MQTT_CONNECT_RETRIES + 1):
        try:
            mqtt_client.connect(host, port, keepalive=60)
            break
        except OSError as e:
            log.error(
                "MQTT-Verbindung fehlgeschlagen (Versuch %d/%d): %s",
                attempt, MQTT_CONNECT_RETRIES, e,
            )
            if attempt == MQTT_CONNECT_RETRIES:
                raise SystemExit(1)
            time.sleep(MQTT_CONNECT_RETRY_DELAY)

    mqtt_client.loop_start()

    def shutdown(signum, frame):
        log.info("Beende Add-on (Signal %d)...", signum)
        mqtt_client.publish(MQTT_AVAILABILITY_TOPIC, "offline", retain=True)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    register_sensors(mqtt_client)

    while True:
        try:
            log.info("=== Starte Abfrage-Zyklus ===")
            run_cycle(mqtt_client, cfg)
            log.info("Nächste Abfrage in %d Stunde(n).", poll_interval_sec // 3600)
        except VivawestError as e:
            log.error("Vivawest-API-Fehler: %s", e)
        except requests.Timeout:
            log.error("API-Timeout – nächster Versuch nach Wartezeit.")
        except requests.ConnectionError as e:
            log.error("Netzwerkfehler: %s – nächster Versuch nach Wartezeit.", e)
        except Exception as e:
            log.exception("Unerwarteter Fehler: %s", e)

        time.sleep(poll_interval_sec)


if __name__ == "__main__":
    main()
