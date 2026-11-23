# AGENTS.md

Hinweise für KI-Agenten und Entwickler, die an diesem Repository arbeiten.

## Projektüberblick

Home-Assistant-**Add-on-Repository** mit genau einem Add-on: **Vivawest**.
Das Add-on meldet sich am Vivawest-Kundenportal an, liest die aktuellen
Verbrauchswerte (Kaltwasser, Warmwasser) aus und veröffentlicht sie per
**MQTT Discovery** als Sensoren in Home Assistant.

- Sprache: Python 3.13 (Alpine-Basisimage von Home Assistant)
- Abhängigkeiten: `requests`, `paho-mqtt` (2.x)
- Kein Build-System, keine Tests, keine CI – nur das Docker-Image, das der HA Supervisor baut.

## Verzeichnisstruktur

```
repository.yaml          # Metadaten des Add-on-Repositorys (Name, URL, Maintainer)
vivawest/                # Das Add-on (Ordnername = Slug)
├── config.yaml          # Add-on-Manifest: Version, Architekturen, Optionen + Schema
├── build.yaml           # Basisimages pro Architektur (BUILD_FROM)
├── Dockerfile           # Installiert requirements, kopiert app/ und run.sh
├── run.sh               # Entrypoint (bashio) → startet python3 /app/main.py
├── DOCS.md              # Benutzerdoku (wird im HA-UI im Tab "Dokumentation" angezeigt)
└── app/
    ├── main.py          # Gesamte Logik (Config, API, MQTT, Poll-Schleife)
    └── requirements.txt # Gepinnte Python-Abhängigkeiten
```

## Architektur von `vivawest/app/main.py`

Ablauf in `main()`:

1. `load_config()` – liest `/data/options.json` (vom Supervisor aus `config.yaml`-Optionen erzeugt).
   Fehlt die Datei (lokale Entwicklung), werden Umgebungsvariablen genutzt.
2. `get_poll_interval()` – Intervall in Stunden, Minimum `MIN_INTERVAL_HOURS = 1`.
3. `build_mqtt_client()` – Client mit Last Will (`vivawest/availability` = `offline`),
   Auto-Reconnect (5–60 s). Verbindung mit bis zu 5 Versuchen.
4. `register_sensors()` – sendet einmalig die Discovery-Configs (retained).
5. Endlosschleife: `get_session_token()` → `get_uvi_current()` → `process_and_publish()` → `sleep`.
   Fehler werden geloggt, die Schleife läuft weiter.
6. SIGTERM/SIGINT → `offline` publizieren, sauber trennen.

### Vivawest-API

| Endpoint | Methode | Header | Zweck |
|---|---|---|---|
| `/api/login` | POST | `Authorization: Bearer <VIVAWEST_BEARER>` | Body `{"email", "password", "v2": true}` → `sessionToken` |
| `/api/uvi/current` | GET | Bearer + `x-session-token` | Aktuelle Messwerte (UVI = unterjährige Verbrauchsinformation) |

Basis-URL: `https://api.kundenportal.vivawest.de`. Der Bearer-Token in `VIVAWEST_BEARER`
ist ein statischer App-Token des Portals (kein Benutzergeheimnis) – nicht entfernen oder ändern,
außer das Portal ändert ihn.

Erwartete Antwortstruktur (nur die genutzten Felder):

```json
{
  "messwerte": {
    "kaltwasser": { "verbrauchswert": 1.23, "ablesedatum": "...", "monat": 10, "jahr": 2026 },
    "warmwasser": { "verbrauchswert": 0.45, "kwh": 12.3, "ablesedatum": "...", "monat": 10, "jahr": 2026 }
  }
}
```

### MQTT-Topics

| Topic | Inhalt |
|---|---|
| `homeassistant/sensor/vivawest_<id>/config` | Discovery-Config (retained) |
| `vivawest/sensor/<id>/state` | JSON `{"value": ..., "ablesedatum", "monat", "jahr"}` (retained) |
| `vivawest/availability` | `online` / `offline` (retained, LWT) |

Sensor-IDs: `kaltwasser_verbrauch` (m³), `warmwasser_verbrauch` (m³), `warmwasser_kwh` (kWh).
Alle hängen am Gerät `vivawest_addon`. **`unique_id`s nicht umbenennen** – sonst entstehen
bei Nutzern doppelte Entitäten und die Historie geht verloren.

## Konventionen

- Code-Kommentare, Docstrings und Log-Meldungen sind **auf Deutsch** – beibehalten.
- Logging über `log = logging.getLogger("vivawest")`, %-Formatierung (`log.info("x %s", y)`), keine f-Strings in Log-Aufrufen.
- Keine Zugangsdaten loggen (Login/Passwort/Session-Token).
- Abhängigkeiten in `requirements.txt` exakt pinnen (`==`).
- Neue Optionen immer an **drei** Stellen ergänzen: `config.yaml` → `options` **und** `schema`,
  sowie im Env-Fallback von `load_config()`. Danach `DOCS.md` aktualisieren.
- Neue Sensoren: `publish_discovery()` in `register_sensors()` + passender `publish_state()` in `process_and_publish()`.

## Release / Versionierung

- Jede Änderung, die Nutzer erhalten sollen, braucht eine **Erhöhung von `version` in
  `vivawest/config.yaml`** – sonst bietet Home Assistant kein Update an.
- Architekturen: `config.yaml` (`arch`) bestimmt, was installierbar ist; `build.yaml` muss für
  jede dort gelistete Architektur ein Basisimage haben.

## Lokal ausführen

Ohne `/data/options.json` liest das Skript Umgebungsvariablen:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r vivawest/app/requirements.txt

VIVAWEST_LOGIN="mail@example.com" \
VIVAWEST_PASSWORD="..." \
MQTT_HOST="localhost" MQTT_PORT=1883 \
MQTT_USER="" MQTT_PASSWORD="" \
POLL_INTERVAL_HOURS=1 \
python3 vivawest/app/main.py
```

Ein lokaler Broker lässt sich z. B. mit `docker run -p 1883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf` starten.
Prüfen: `mosquitto_sub -h localhost -t 'vivawest/#' -t 'homeassistant/#' -v`.

Docker-Build lokal (Beispiel amd64):

```bash
docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.21 -t vivawest vivawest/
```

## Bekannte Punkte / mögliche Verbesserungen

- `mqtt.CallbackAPIVersion.VERSION1` ist in paho-mqtt 2.x deprecated; Umstieg auf `VERSION2`
  erfordert angepasste Callback-Signaturen (`on_connect(client, userdata, flags, reason_code, properties)`).
- Discovery wird nur einmal beim Start gesendet, nicht erneut nach einem Broker-Neustart
  (durch `retain=True` meist unkritisch).
- Bei jedem Zyklus wird neu eingeloggt; ein Token-Caching existiert nicht.
- Keine automatisierten Tests vorhanden. Bei Änderungen an `process_and_publish()` mit einem
  Beispiel-Dict manuell testen.
