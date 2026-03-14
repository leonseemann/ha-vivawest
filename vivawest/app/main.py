"""
Vivawest Home Assistant Add-on
Liest Kaltwasser- und Warmwasser-Messwerte und veröffentlicht sie
als HA-Sensoren via MQTT Discovery.
"""

import json
import logging
import os
import time

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
VIVAWEST_BASE         = "https://api.kundenportal.vivawest.de"
VIVAWEST_BEARER       = "7d2e1b552a69092edff3b2212c5985e051a7e934"
MQTT_DISCOVERY_PREFIX = "homeassistant"
MIN_INTERVAL_HOURS    = 1


# ---------------------------------------------------------------------------
# Konfiguration aus /data/options.json (Home Assistant Add-on Standard)
# ---------------------------------------------------------------------------
def load_config() -> dict:
    options_path = "/data/options.json"
    if not os.path.exists(options_path):
        log.warning("options.json nicht gefunden – nutze Umgebungsvariablen.")
        return {
            "login":               os.environ.get("VIVAWEST_LOGIN", ""),
            "password":            os.environ.get("VIVAWEST_PASSWORD", ""),
            "mqtt_host":           os.environ.get("MQTT_HOST", "core-mosquitto"),
            "mqtt_port":           int(os.environ.get("MQTT_PORT", "1883")),
            "mqtt_user":           os.environ.get("MQTT_USER", ""),
            "mqtt_password":       os.environ.get("MQTT_PASSWORD", ""),
            "poll_interval_hours": int(os.environ.get("POLL_INTERVAL_HOURS", "1")),
        }
    with open(options_path) as f:
        return json.load(f)


def get_poll_interval(cfg: dict) -> int:
    """Liest das Intervall aus der Config, erzwingt Minimum 1 Stunde."""
    hours = int(cfg.get("poll_interval_hours", MIN_INTERVAL_HOURS))
    if hours < MIN_INTERVAL_HOURS:
        log.warning(
            "Abfrageintervall %dh ist unter dem Minimum (%dh) – setze auf %dh.",
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
        timeout=15,
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
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# MQTT Helpers
# ---------------------------------------------------------------------------
def publish_discovery(client: mqtt.Client, sensor_id: str, name: str,
                       unit: str, device_class: str | None = None,
                       state_class: str | None = None,
                       icon: str | None = None) -> None:
    topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/vivawest_{sensor_id}/config"
    payload = {
        "name": name,
        "unique_id": f"vivawest_{sensor_id}",
        "state_topic": f"vivawest/sensor/{sensor_id}/state",
        "unit_of_measurement": unit,
        "value_template": "{{ value_json.value }}",
        "json_attributes_topic": f"vivawest/sensor/{sensor_id}/state",
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

    kw = messwerte.get("kaltwasser")
    if kw:
        publish_state(client, "kaltwasser_verbrauch", kw.get("verbrauchswert"), {
            "ablesedatum": kw.get("ablesedatum"),
            "monat":       kw.get("monat"),
            "jahr":        kw.get("jahr"),
        })
    else:
        log.warning("Keine Kaltwasser-Daten in der Antwort.")

    ww = messwerte.get("warmwasser")
    if ww:
        publish_state(client, "warmwasser_verbrauch", ww.get("verbrauchswert"), {
            "ablesedatum": ww.get("ablesedatum"),
            "monat":       ww.get("monat"),
            "jahr":        ww.get("jahr"),
        })
        publish_state(client, "warmwasser_kwh", ww.get("kwh"), {
            "ablesedatum": ww.get("ablesedatum"),
            "monat":       ww.get("monat"),
            "jahr":        ww.get("jahr"),
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
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()

    login    = cfg["login"]
    password = cfg["password"]

    if not login or not password:
        log.error("Login und/oder Passwort fehlen in der Konfiguration!")
        raise SystemExit(1)

    poll_interval_sec = get_poll_interval(cfg)
    log.info("Abfrageintervall: %d Stunde(n).", poll_interval_sec // 3600)

    # MQTT verbinden
    mqtt_client = mqtt.Client(client_id="vivawest_addon")
    if cfg.get("mqtt_user"):
        mqtt_client.username_pw_set(cfg["mqtt_user"], cfg.get("mqtt_password", ""))

    log.info("Verbinde mit MQTT-Broker %s:%s...", cfg.get("mqtt_host", "core-mosquitto"), cfg.get("mqtt_port", 1883))
    mqtt_client.connect(cfg.get("mqtt_host", "core-mosquitto"), int(cfg.get("mqtt_port", 1883)), keepalive=60)
    mqtt_client.loop_start()

    register_sensors(mqtt_client)

    while True:
        try:
            log.info("=== Starte Abfrage-Zyklus ===")
            token    = get_session_token(login, password)
            uvi_data = get_uvi_current(token)
            process_and_publish(mqtt_client, uvi_data)
            log.info("Nächste Abfrage in %d Stunde(n).", poll_interval_sec // 3600)
        except requests.HTTPError as e:
            log.error("HTTP-Fehler: %s", e)
        except requests.Timeout:
            log.error("API-Timeout – nächster Versuch nach Wartezeit.")
        except Exception as e:
            log.exception("Unerwarteter Fehler: %s", e)

        time.sleep(poll_interval_sec)


if __name__ == "__main__":
    main()
