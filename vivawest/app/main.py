"""
Vivawest Home Assistant Add-on
Liest Kaltwasser- und Warmwasser-Messwerte und veröffentlicht sie
als HA-Sensoren via MQTT Discovery.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from typing import Optional

import paho.mqtt.client as mqtt
import requests

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
VIVAWEST_BASE = "https://api.kundenportal.vivawest.de"
VIVAWEST_BEARER = "7d2e1b552a69092edff3b2212c5985e051a7e934"
MQTT_DISCOVERY_PREFIX = "homeassistant"
MQTT_AVAILABILITY_TOPIC = "vivawest/availability"
MIN_INTERVAL_HOURS = 1
REQUEST_TIMEOUT = 15
MQTT_CONNECT_RETRIES = 5
MQTT_CONNECT_RETRY_DELAY = 10


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
# Vivawest API
# ---------------------------------------------------------------------------
def get_session_token(login: str, password: str) -> str:
    """Gibt den sessionToken zurück oder wirft eine Exception."""
    log.info("Hole Session-Token...")
    resp = requests.post(
        f"{VIVAWEST_BASE}/api/login",
        headers={
            "Accept": "*/*",
            "Authorization": f"Bearer {VIVAWEST_BEARER}",
            "Content-Type": "application/json",
        },
        json={"email": login, "password": password, "v2": True},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    token = resp.json().get("sessionToken")
    if not token:
        raise ValueError("sessionToken fehlt in der Login-Antwort")
    log.info("Session-Token erhalten.")
    return token


def get_uvi_current(session_token: str) -> dict:
    """Ruft /api/uvi/current ab und gibt das JSON-Dict zurück."""
    log.info("Rufe UVI-Daten ab...")
    resp = requests.get(
        f"{VIVAWEST_BASE}/api/uvi/current",
        headers={
            "Authorization": f"Bearer {VIVAWEST_BEARER}",
            "x-session-token": session_token,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# MQTT Helpers
# ---------------------------------------------------------------------------
def publish_discovery(client: mqtt.Client, sensor_id: str, name: str,
                      unit: str, device_class: Optional[str] = None,
                      state_class: Optional[str] = None,
                      icon: Optional[str] = None) -> None:
    topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/vivawest_{sensor_id}/config"
    payload = {
        "name": name,
        "unique_id": f"vivawest_{sensor_id}",
        "state_topic": f"vivawest/sensor/{sensor_id}/state",
        "unit_of_measurement": unit,
        "value_template": "{{ value_json.value }}",
        "json_attributes_topic": f"vivawest/sensor/{sensor_id}/state",
        "availability_topic": MQTT_AVAILABILITY_TOPIC,
        "device": {
            "identifiers": ["vivawest_addon"],
            "name": "Vivawest",
            "model": "Kundenportal Add-on",
            "manufacturer": "Vivawest",
        },
    }
    if device_class:
        payload["device_class"] = device_class
    if state_class:
        payload["state_class"] = state_class
    if icon:
        payload["icon"] = icon
    client.publish(topic, json.dumps(payload), retain=True)
    log.info("Discovery gesendet: %s", name)


def publish_state(client: mqtt.Client, sensor_id: str, value, attributes: dict) -> None:
    topic = f"vivawest/sensor/{sensor_id}/state"
    payload = {"value": value, **attributes}
    client.publish(topic, json.dumps(payload), retain=True)
    log.info("State veröffentlicht: %s = %s", sensor_id, value)


# ---------------------------------------------------------------------------
# Daten verarbeiten und veröffentlichen
# ---------------------------------------------------------------------------
def process_and_publish(client: mqtt.Client, uvi_data: dict) -> None:
    messwerte = uvi_data.get("messwerte", {})
    if not messwerte:
        log.warning("Keine 'messwerte' in der API-Antwort. Rohdaten: %s", uvi_data)
        return

    kw = messwerte.get("kaltwasser")
    if kw:
        publish_state(client, "kaltwasser_verbrauch", kw.get("verbrauchswert"), {
            "ablesedatum": kw.get("ablesedatum"),
            "monat": kw.get("monat"),
            "jahr": kw.get("jahr"),
        })
    else:
        log.warning("Keine Kaltwasser-Daten in der Antwort.")

    ww = messwerte.get("warmwasser")
    if ww:
        publish_state(client, "warmwasser_verbrauch", ww.get("verbrauchswert"), {
            "ablesedatum": ww.get("ablesedatum"),
            "monat": ww.get("monat"),
            "jahr": ww.get("jahr"),
        })
        publish_state(client, "warmwasser_kwh", ww.get("kwh"), {
            "ablesedatum": ww.get("ablesedatum"),
            "monat": ww.get("monat"),
            "jahr": ww.get("jahr"),
        })
    else:
        log.warning("Keine Warmwasser-Daten in der Antwort.")


def register_sensors(client: mqtt.Client) -> None:
    publish_discovery(client, "kaltwasser_verbrauch",
                      name="Vivawest Kaltwasser Verbrauch",
                      unit="m³", device_class="water",
                      state_class="total_increasing",
                      icon="mdi:water")
    publish_discovery(client, "warmwasser_verbrauch",
                      name="Vivawest Warmwasser Verbrauch",
                      unit="m³", device_class="water",
                      state_class="total_increasing",
                      icon="mdi:water-thermometer")
    publish_discovery(client, "warmwasser_kwh",
                      name="Vivawest Warmwasser Energie",
                      unit="kWh", device_class="energy",
                      state_class="total_increasing",
                      icon="mdi:lightning-bolt")


# ---------------------------------------------------------------------------
# MQTT Client aufbauen
# ---------------------------------------------------------------------------
def build_mqtt_client(cfg: dict) -> mqtt.Client:
    mqtt_client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
        client_id="vivawest_addon",
    )

    if cfg.get("mqtt_user"):
        mqtt_client.username_pw_set(cfg["mqtt_user"], cfg.get("mqtt_password", ""))

    # Last Will and Testament – HA zeigt "Nicht verfügbar" bei unerwartetem Absturz
    mqtt_client.will_set(MQTT_AVAILABILITY_TOPIC, payload="offline", retain=True)

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            log.info("MQTT verbunden.")
            client.publish(MQTT_AVAILABILITY_TOPIC, "online", retain=True)
        else:
            log.error("MQTT Verbindung fehlgeschlagen (RC=%d).", rc)

    def on_disconnect(client, userdata, rc):
        if rc != 0:
            log.warning(
                "MQTT unerwartet getrennt (RC=%d) – automatische Wiederverbindung aktiv.", rc
            )

    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.reconnect_delay_set(min_delay=5, max_delay=60)

    return mqtt_client


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()

    login = cfg["login"]
    password = cfg["password"]

    if not login or not password:
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
            token = get_session_token(login, password)
            uvi_data = get_uvi_current(token)
            process_and_publish(mqtt_client, uvi_data)
            log.info("Nächste Abfrage in %d Stunde(n).", poll_interval_sec // 3600)
        except requests.HTTPError as e:
            log.error("HTTP-Fehler: %s", e)
        except requests.Timeout:
            log.error("API-Timeout – nächster Versuch nach Wartezeit.")
        except requests.ConnectionError as e:
            log.error("Netzwerkfehler: %s – nächster Versuch nach Wartezeit.", e)
        except Exception as e:
            log.exception("Unerwarteter Fehler: %s", e)

        time.sleep(poll_interval_sec)


if __name__ == "__main__":
    main()