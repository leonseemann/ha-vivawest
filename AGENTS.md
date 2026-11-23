# AGENTS.md

Hinweise für KI-Agenten und Entwickler, die an diesem Repository arbeiten.

## Projektüberblick

Home-Assistant-**Add-on-Repository** mit genau einem Add-on: **Vivawest**.
Das Add-on meldet sich am Vivawest-Kundenportal an, liest die Monatsverbräuche
(Heizung, Kaltwasser, Warmwasser) aus und

1. veröffentlicht den **letzten Monatswert** per **MQTT Discovery** als Sensoren,
2. importiert alle verfügbaren Monatsverbräuche **rückwirkend in die
   Langzeitstatistik**, gleichmäßig auf die Stunden des jeweiligen Monats verteilt
   (für das Energie-Dashboard).

- Sprache: Python 3.13 (Alpine-Basisimage von Home Assistant)
- Abhängigkeiten: `requests`, `paho-mqtt` (2.x, Callback-API v2), `websocket-client`
- Kein Build-System, keine automatisierten Tests, keine CI – nur das Docker-Image, das der HA Supervisor baut.

## Verzeichnisstruktur

```
repository.yaml              # Metadaten des Add-on-Repositorys
vivawest/                    # Das Add-on (Ordnername = Slug)
├── config.yaml              # Manifest: Version, Architekturen, homeassistant_api, Optionen + Schema
├── build.yaml               # Basisimages pro Architektur (BUILD_FROM)
├── Dockerfile               # Installiert requirements, kopiert app/ und run.sh
├── run.sh                   # Entrypoint (bashio) → python3 /app/main.py
├── DOCS.md                  # Benutzerdoku (Tab "Dokumentation" im HA-UI)
└── app/
    ├── main.py              # Config, MQTT-Sensoren, Poll-Schleife
    ├── vivawest_api.py      # REST-Client für das Kundenportal
    ├── ha_statistics.py     # Monatsverteilung + Import über HA-WebSocket-API
    └── requirements.txt     # Gepinnte Python-Abhängigkeiten
```

> `ha_statistics.py` darf nicht `statistics.py` heißen – das würde das gleichnamige
> Standard-Modul überdecken.

## Ablauf (`main.py`)

1. `load_config()` – liest `/data/options.json`; fehlt sie (lokal), Umgebungsvariablen.
2. MQTT verbinden (Last Will `vivawest/availability` = `offline`), Discovery senden.
3. Schleife alle `poll_interval_hours` → `run_cycle()`:
   - `VivawestClient` (Kontextmanager: Login → Abfragen → Logout)
   - `get_current()` → `process_and_publish()` (MQTT)
   - `get_history(typ)` für jede Verbrauchsart → `import_all()` (Statistik)
   - Fehler werden geloggt, die Schleife läuft weiter. Ein Fehler im Statistik-Import
     verhindert die MQTT-Aktualisierung nicht.

## Vivawest-API (`vivawest_api.py`)

Basis-URL: `https://api.kundenportal.vivawest.de/rest/` (bis 11/2026 war es `/api/` mit
festem Bearer-Token – das gibt es nicht mehr). Die Endpunkte stammen aus dem
Web-Frontend (`kundenportal.vivawest.de/assets/*.js`), es gibt keine offizielle Doku.

| Endpoint | Methode | Zweck |
|---|---|---|
| `login` | POST `{"email", "password"}` | Antwort enthält `sessionId`, `expireDate`, Vertragsdaten |
| `uvi/current` | GET | Letzter Monatswert je Verbrauchsart + `types` |
| `uvi/current/<typ>` | GET | Monatsverlauf (ca. 10–12 Monate) + Werte pro Einzelzähler |
| `contracts/conditions` | GET | Mietkonditionen als Zeiträume (vergangen/aktuell/zukünftig) |
| `additional-costs/settlements`, `additional-costs/details/<Ident>` | GET | Nebenkostenabrechnungen (derzeit nicht genutzt) |
| `logout` | PUT | Session beenden |

- Authentifizierung: Header `X-Session-Id: <sessionId>` (Cookies sind nicht nötig).
- Zusätzlich sendet der Client `X-Web-Version` (Version des Web-Frontends).
- Fehlerantworten sind JSON: `{"exception": "SessionException" | "WebserviceException", "code", "message"}`.
- `typ` ∈ `heizenergie`, `kaltwasser`, `warmwasser`.

Monatseintrag (aus `uvi/current` → `messwerte.<typ>` bzw. `uvi/current/<typ>` → `messwerte[]`):

```json
{ "monat": 10, "jahr": 2026, "ablesedatum": "2026-11-02T00:00:00+01:00",
  "verbrauchswert": 80.63, "kwh": 82.63, "differenz_liegenschaft_qm_prozent": 4.59 }
```

- `verbrauchswert` = **Verbrauch des Monats** (Summe aller Zähler), kein Zählerstand.
  Kalt-/Warmwasser in m³, Heizung in Verbrauchseinheiten (VE, `heizenergieEinheit`).
- `kwh` = Energie (Warmwasser, Heizung). Bei Heizung ein vom Portal umgerechneter Wert.
- `contracts/conditions`: Liste von Zeiträumen `{Gueltig, GueltigVon, GueltigBis, GrossAmountdec,
  ConditionItems.results[{Condtype, Xcondtypel, GrossAmountdec}]}`. Bekannte `Condtype`:
  `1000` Grundmiete, `3760` TV-Versorgung, `4000` VZ Betriebskosten, `4010` VZ Heizkosten.
  Enthält auch `Recnnr` (Vertragsnummer) – nicht loggen.
- Preise pro m³/kWh liefert das Portal nicht; Heiz-/Wasserkostenabrechnungen sind
  (Stand 11/2026) noch nicht vorhanden.
- Die Antworten enthalten unter `contract` umfangreiche **persönliche Daten** –
  `get_current()` verwirft sie. Nie loggen oder persistieren.

## MQTT-Sensoren

| Topic | Inhalt |
|---|---|
| `homeassistant/sensor/vivawest_<id>/config` | Discovery-Config (retained) |
| `vivawest/sensor/<id>/state` | JSON `{"value", "ablesedatum", "monat", "jahr", ["vergleich_liegenschaft_prozent"]}` (retained) |
| `vivawest/availability` | `online` / `offline` (retained, LWT) |

Definiert in `SENSORS` (`main.py`): `kaltwasser_verbrauch`, `warmwasser_verbrauch`,
`warmwasser_kwh`, `heizenergie_kwh`, `heizenergie_ve`. Gerät: `vivawest_addon`.
**`sensor_id`s nicht umbenennen** (Teil der `unique_id`).

Mietkonditionen: `COST_SENSORS` (`miete_gesamt`, `grundmiete`, `vorauszahlung_betriebskosten`,
`vorauszahlung_heizkosten`; `field` = `Condtype` bzw. `gesamt`). `process_conditions()` wählt
den aktuellen Zeitraum (`Gueltig`, sonst per Datum) und setzt `naechster_betrag` /
`naechste_aenderung` aus dem ersten zukünftigen Zeitraum mit abweichendem Betrag.
Ein Fehler beim Abruf der Konditionen wird nur als Warnung geloggt.

Die Sensoren haben bewusst **keine `state_class`**: Sie zeigen den Verbrauch des letzten
Monats, keinen Zählerstand. Für Verläufe ist die Langzeitstatistik zuständig.

## Langzeitstatistik (`ha_statistics.py`)

- Externe Statistiken `vivawest:kaltwasser`, `vivawest:warmwasser`,
  `vivawest:warmwasser_energie`, `vivawest:heizenergie` (Definition: `STATISTICS`).
- Jeder Monatswert wird gleichmäßig auf alle Stunden des Monats in der HA-Zeitzone
  verteilt (`distribute()`, sommer-/winterzeitfest). `sum`/`state` sind kumulativ.
- Import über WebSocket `recorder/import_statistics`. Im Add-on über
  `ws://supervisor/core/websocket` mit `SUPERVISOR_TOKEN` (`homeassistant_api: true`
  in `config.yaml` ist dafür nötig). Lokal über `HA_URL` + `HA_TOKEN`.
- Metadaten: zuerst neues Format (`mean_type`, `unit_class`), bei Ablehnung Fallback auf
  `has_mean` (ältere HA-Versionen).
- **Zustand** in `/data/state.json`: pro Statistik `{"YYYY-MM": {"value", "sum_end"}}`.
  `plan_import()` importiert nur neue/geänderte Monate und berechnet ab dem ersten
  geänderten Monat alle Folgemonate neu, damit die Summe stetig bleibt.
  Geht die Datei verloren, beginnt die Summe wieder bei 0. Ältere, nicht mehr von der API
  gelieferte Monate bleiben dann mit alter Summe stehen, und es entsteht ein Sprung.

## Konventionen

- Code-Kommentare, Docstrings und Log-Meldungen sind **auf Deutsch**.
- Logging über `logging.getLogger("vivawest")`, %-Formatierung, keine f-Strings in Log-Aufrufen.
- Keine Zugangsdaten, Session-IDs oder Vertragsdaten loggen.
- Abhängigkeiten in `requirements.txt` exakt pinnen (`==`).
- Neue Optionen an **drei** Stellen: `config.yaml` (`options` **und** `schema`) und
  Env-Fallback in `load_config()`. Danach `DOCS.md` aktualisieren.
- Neuer Verbrauchssensor: Eintrag in `SENSORS`; neuer Kostensensor: `COST_SENSORS`. Neue Statistik: `STATISTICS`.

## Release / Versionierung

- Jede Änderung für Nutzer braucht eine **Erhöhung von `version` in `vivawest/config.yaml`**.
- `config.yaml` (`arch`) bestimmt die installierbaren Architekturen; `build.yaml` muss für jede ein Basisimage haben.

## Lokal ausführen

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r vivawest/app/requirements.txt

VIVAWEST_LOGIN="mail@example.com" VIVAWEST_PASSWORD="..." \
MQTT_HOST="localhost" MQTT_PORT=1883 \
HA_URL="http://homeassistant.local:8123" HA_TOKEN="<long-lived-token>" \
python3 vivawest/app/main.py
```

- Ohne `HA_URL`/`HA_TOKEN` wird der Statistik-Import übersprungen (Warnung im Log).
  `IMPORT_STATISTICS=false` schaltet ihn ganz ab.
- Lokal liegt der Zustand in `./state.json` (git-ignoriert), überschreibbar mit `STATE_FILE`.
- Lokaler Broker: `docker run -p 1883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf`,
  prüfen mit `mosquitto_sub -h localhost -t 'vivawest/#' -t 'homeassistant/#' -v`.
- Docker-Build: `docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.21 -t vivawest vivawest/`

## Bekannte Punkte

- Die API ist inoffiziell und kann sich jederzeit ändern (zuletzt 11/2026: `/api/` → `/rest/`).
  Bei 404/401 zuerst das Web-Frontend auf neue Pfade prüfen.
- Heizungsdaten liefert das Portal nur für wenige zurückliegende Monate.
- Discovery wird nur beim Start gesendet (retained, daher meist unkritisch).
