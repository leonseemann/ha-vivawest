# AGENTS.md

Hinweise für KI-Agenten und Entwickler, die an diesem Repository arbeiten.

## Projektüberblick

**Custom Integration** `vivawest` für Home Assistant (installierbar über den Marketplace
bzw. HACS). Sie meldet sich am Vivawest-Kundenportal an und

1. stellt den **letzten Monatsverbrauch** (Heizung, Kalt-, Warmwasser) und die aktuellen
   **Mietkonditionen** als Sensoren bereit,
2. importiert die Monatsverbräuche **rückwirkend als externe Statistik**, gleichmäßig auf
   die Stunden des jeweiligen Monats verteilt (Energie-Dashboard).

Bis Version 1.2.0 war das Projekt ein Supervisor-Add-on mit MQTT. Das wurde entfernt
(siehe Git-Historie). Der Marketplace installiert keine Add-ons, und ein Repo mit Add-ons
lässt sich dort nicht hinzufügen. **Keine Add-on-Dateien (`repository.yaml`, `config.yaml`)
wieder einführen.**

- Mindestversion Home Assistant: siehe `hacs.json` (`homeassistant`)
- Keine externen Python-Abhängigkeiten (`requirements: []`), HTTP über aiohttp von HA
- Python ≥ 3.14 (Vorgabe von Home Assistant)

## Struktur

```
custom_components/vivawest/
├── manifest.json        # domain, version, config_flow, dependencies: [recorder]
├── __init__.py          # async_setup_entry / async_unload_entry
├── api.py               # VivawestClient (aiohttp), VivawestData, Fehlerklassen
├── coordinator.py       # VivawestCoordinator (12 h), ruft Statistik-Import auf
├── statistics.py        # Monatsverteilung, plan_import(), async_import_statistics()
├── forecast.py          # Prognose der nächsten Abrechnung (Nachzahlung/Guthaben)
├── sensor.py            # SENSORS (EntityDescriptions), Konditions-Hilfsfunktionen
├── config_flow.py       # user- und reauth-Schritt, Optionen (Preise)
├── diagnostics.py       # Diagnose mit async_redact_data
├── icons.json           # Icons je translation_key
├── strings.json         # Quelle für Übersetzungen (Englisch, mit [%key:...%])
└── translations/        # en.json (aufgelöst), de.json – Custom Integrations nutzen nur diese
hacs.json
tests/                   # pytest-homeassistant-custom-component
.github/workflows/validate.yml   # hassfest, HACS-Validierung, pytest
```

## Vivawest-API (`api.py`)

Inoffiziell. Die Endpunkte stammen aus dem Web-Frontend (`kundenportal.vivawest.de/assets/*.js`).
Basis-URL `https://api.kundenportal.vivawest.de/rest/` (bis 11/2026: `/api/` mit festem Bearer-Token).

| Endpoint | Methode | Zweck |
|---|---|---|
| `login` | POST `{"email", "password"}` | → `sessionId`, `partnerId`, `expireDate`, Vertragsdaten |
| `uvi/current` | GET | `types`, `messwerte.<typ>` (letzter Monat), `heizenergieEinheit` |
| `uvi/current/<typ>` | GET | `messwerte[]`: Monatsverlauf (neueste zuerst), `geraete[]`: Einzelzähler |
| `contracts/conditions` | GET | Mietkonditionen als Zeiträume |
| `additional-costs/settlements` | GET | Abrechnungsarten mit Zeiträumen (`BillingOverview2Periods.results[{Ident, AbrechnungBis}]`) |
| `additional-costs/details/<Ident>` | GET | `settlement` (Ergebnis, Kosten, Prognose, Positionen), `prepayments[]` |
| `postbox/unread` | GET | `UnreadPostbox`, `UnreadArchiv` |
| `damages/reports?status=progress\|closed&limit&offset` | GET | Schadensmeldungen (`results[]`) |
| `notification-badges` | GET | `{<Vertragsnummer>: {count, badges[]}}` |
| `logout` | PUT | Session beenden |

- Auth-Header `X-Session-Id`, zusätzlich `X-Web-Version`. Cookies sind nicht nötig.
- Fehler sind JSON `{"exception", "code", "message"}`. Falsches Passwort → 403
  `WebserviceException`, ohne Session → 401 `SessionException`. Beides wird zu
  `VivawestAuthError` → `ConfigEntryAuthFailed` → Reauth-Flow.
- Alle Endpunkte außer Login und `uvi/*` werden über `_optional()` abgefragt: Fehler
  (außer 401/403) setzen das Feld in `VivawestData` auf `None` / leer, statt den Abruf abzubrechen.
- Nicht genutzt: `appwash` (500 ohne Waschküche), `maintenance`, `documents`, `contract-components/current`,
  `contracts/service-partner` (persönliche Daten), `dashboard/<Vertragsnummer>`.
- `typ` ∈ `heizenergie`, `kaltwasser`, `warmwasser`.
- Monatseintrag: `{monat, jahr, ablesedatum, verbrauchswert, [kwh], [differenz_liegenschaft_qm_prozent]}`.
  `verbrauchswert` ist der **Monatsverbrauch** (Summe aller Zähler), kein Zählerstand.
  Heizung in VE, `kwh` vom Portal umgerechnet.
- Einzelzähler: `uvi/current/<typ>` → `geraete[{geraet: {id, nr, typ (KWZ/WWZ/ELT), raum, einheit (CBM/""),
  umrech_faktor, wohneinheit (Adressen!)}, messwerte[]}]`, Monate aufsteigend. Monate ohne Ablesung haben
  nur `fehler_code`. Sonst `ablesewert` / `verbrauchswert` (Zählerstand), `verbrauchswert_delta_monatsende`
  (Monatsverbrauch), bei Warmwasser zusätzlich `verbrauchswert_kwh` / `..._delta_monatsende_kwh`.
  `_slim_meter()` verwirft `wohneinheit` und die wiederholten `geraet`-Blöcke.
- Abrechnung: `AeErgebnis` > 0 = Nachzahlung, sonst Guthaben (wie im Portal). Datumsfelder können
  Platzhalter wie `-001-11-30…` enthalten → `_valid_date()`.
- Konditionen: `[{Gueltig, GueltigVon, GueltigBis, GrossAmountdec, ConditionItems.results[{Condtype,
  Xcondtypel, GrossAmountdec}]}]`. `Condtype`: `1000` Grundmiete, `3760` TV, `4000` VZ Betriebskosten,
  `4010` VZ Heizkosten.
- Preise pro Einheit liefert das Portal nicht.
- Antworten enthalten **persönliche Daten** (`contract`-Block, Namen, IBAN, Geburtsdaten, `Recnnr`).
  `VivawestData` übernimmt sie nicht. Nie loggen, nie in Tests oder Fixtures übernehmen.

## Statistik (`statistics.py`)

- Externe Statistiken `vivawest:kaltwasser`, `vivawest:warmwasser`, `vivawest:warmwasser_energie`,
  `vivawest:heizenergie` (`STATISTICS`). **IDs nicht ändern**, Nutzer haben sie im Energie-Dashboard.
- `distribute()` verteilt einen Monatswert auf alle Stunden des Monats in der HA-Zeitzone
  (sommer-/winterzeitfest). `state` = `sum` = kumulativ.
- Es gibt keine Statusdatei: `async_import_statistics()` liest die Monatssummen aus dem
  Recorder (`statistics_during_period`, Periode `month`). `plan_import()` vergleicht sie mit
  den API-Werten und importiert ab dem ersten neuen oder abweichenden Monat (Toleranz 0,005)
  alle Folgemonate neu, Startsumme = Summe am Ende des Vormonats.
- Kosten: Das Energie-Dashboard rechnet für externe Statistiken keine Preise. Sind in den
  Optionen (`OptionsFlowWithReload`, Keys `CONF_PRICE_*` in `const.py`, `StatDef.price_key`) Preise
  gesetzt, entsteht zusätzlich `<id>_kosten` (Monatswert × Preis, Einheit `hass.config.currency`,
  `unit_class=None`). Gleicher Import über `_import_series()`; Preisänderung → alle API-Monate neu.
- Metadaten mit `mean_type=StatisticMeanType.NONE` und `unit_class` (`has_mean` gibt es seit HA 2026.4 nicht mehr).

## Abrechnungsprognose (`forecast.py`)

- `FORECAST_KINDS`: Betriebskosten (Condtype `4000`) und Heizkosten (`4010`). Abrechnungsarten
  mit „heiz“ im Namen gehören zu den Heizkosten, alle anderen (auch Wasserkosten) zu den Betriebskosten.
- Zeitraum = ein Jahr ab dem Tag nach dem neuesten `AbrechnungBis`. Ergebnis = Fixkosten +
  Verbrauchskosten − Vorauszahlungen (Monatsbeträge aus den Konditionen; Monate vor dem ersten
  bekannten Zeitraum bekommen dessen Betrag). > 0 = Nachzahlung, wie `AeErgebnis`.
- Verbrauchskosten: Portal-Verlauf (`Series` → `STATISTICS`) × Preis aus den Optionen
  (`VivawestData.prices`, vom Coordinator gesetzt). Fehlende Monate: `project()` –
  Heizung nach `HEATING_PROFILE` (Gradtagszahlen, mind. `MIN_HEATING_SHARE` ‰ bekannt), sonst linear.
- Fixkosten aus `KostenMvPrognose` (sonst `KostenMv`): Heizung `1 − HEATING_CONSUMPTION_SHARE`,
  Betriebskosten ohne Positionen mit „wasser“. Fehlt ein Preis → `methode = "vivawest"`.
- Bewusst kein Vorjahresvergleich: Die Prognose soll auf den aktuellen Werten beruhen.

## Sensoren (`sensor.py`)

- `SENSORS`: `VivawestSensorDescription` mit `value_fn` / `attrs_fn`. Namen über
  `translation_key` (`has_entity_name`). Unique-ID `<partnerId>_<key>`. **Keys nicht umbenennen.**
- Verbrauchssensoren bewusst **ohne `state_class`** (Monatswert, kein Zähler). Die
  Langzeitstatistik kommt aus dem Import.
- Kostensensoren: `select_periods()` wählt den aktuellen Zeitraum (Flag `Gueltig`, sonst per
  Datum). `naechster_betrag` / `naechste_aenderung` stammen aus dem ersten zukünftigen
  Zeitraum mit abweichendem Betrag (fehlende Position = 0 €).
- Ein Sensor ist unavailable, wenn `value_fn` `None` liefert (außer `allow_none=True`, z. B.
  Handwerkertermin → „unbekannt“).
- Dynamische Sensoren: `meter_descriptions()` (Key `zaehler_<geraet.id>[_energie]`) und
  `settlement_descriptions()` (Key `abrechnung_<slug(Art)>`). Namen über `translation_key` +
  `placeholders` (`{raum}`, `{art}`). `async_setup_entry` legt bei jedem Update neu
  hinzugekommene an. Zählersensoren haben `state_class: total_increasing`.
- Icons in `icons.json`.

## Konventionen

- Kommentare, Docstrings, Logmeldungen: Deutsch. UI-Texte: `strings.json` + `translations/en.json`
  (Englisch) und `translations/de.json`. Bei neuen Texten **alle drei** pflegen. `en.json`
  enthält die aufgelösten `[%key:...%]`-Referenzen, weil Custom Integrations `strings.json` nicht auswerten.
- Logging über `LOGGER` aus `const.py`.
- Neuer Sensor: Eintrag in `SENSORS` und Übersetzungen unter `entity.sensor.<key>`.
- Neue Statistik: Eintrag in `STATISTICS`.
- Version in `manifest.json` bei jedem Release erhöhen und als GitHub-Release taggen (`v2.0.0`).

## Tests

```bash
python3 -m venv .venv && source .venv/bin/activate   # Python ≥ 3.14
pip install -r requirements_test.txt
pytest -q
```

- `tests/conftest.py`: `make_data()` liefert **erfundene** Daten im echten Format. `mock_client`
  patcht `VivawestClient` in Coordinator und Config Flow. `recorder_mock` ist für alle Tests aktiv,
  weil die Integration vom Recorder abhängt.
- Entity-IDs hängen von der Sprache ab. In Tests deshalb über die Unique-ID suchen (`get_state()`).
- `pytest-homeassistant-custom-component` in `requirements_test.txt` legt die HA-Version der Tests fest.

## Bekannte Punkte

- Bei 404/401 vom Portal zuerst das Web-Frontend auf geänderte Pfade prüfen (JS-Bundle nach
  `.get(\`/` bzw. `.post(\`/` durchsuchen).
- Heizungsdaten liefert das Portal nur für wenige zurückliegende Monate.
- Mehrere Mietverträge pro Konto werden nicht unterschieden (es wird der aktuelle Vertrag des Portals genutzt).
