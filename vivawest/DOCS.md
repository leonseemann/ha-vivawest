# Vivawest Add-on

Dieses Add-on liest deine Heizungs- und Wasserverbräuche aus dem
[Vivawest Kundenportal](https://kundenportal.vivawest.de) und stellt sie in
Home Assistant bereit:

- als **Sensoren** mit dem Verbrauch des letzten Monats (per MQTT) und
- als **Langzeitstatistik** für das Energie-Dashboard. Jeder Monatsverbrauch wird
  rückwirkend gleichmäßig auf den jeweiligen Monat verteilt.

Außerdem zeigt es die aktuelle **Miete und die Vorauszahlungen** samt geplanten Änderungen.

## Voraussetzungen

- Ein Konto im Vivawest Kundenportal mit freigeschalteter Verbrauchsinformation (UVI).
- Ein MQTT-Broker, z. B. das offizielle **Mosquitto broker** Add-on.
- Die **MQTT-Integration** in Home Assistant.

## Installation

1. In Home Assistant: **Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories**.
2. URL hinzufügen: `https://github.com/leonseemann/ha-vivawest`
3. Add-on **Vivawest** installieren.
4. Konfiguration ausfüllen (siehe unten) und Add-on starten.

## Konfiguration

| Option | Beschreibung | Standard |
|---|---|---|
| `login` | E-Mail-Adresse des Kundenportal-Kontos | – (Pflicht) |
| `password` | Passwort des Kundenportal-Kontos | – (Pflicht) |
| `mqtt_host` | Hostname des MQTT-Brokers | `core-mosquitto` |
| `mqtt_port` | Port des MQTT-Brokers | `1883` |
| `mqtt_user` | MQTT-Benutzername (leer = ohne Anmeldung) | leer |
| `mqtt_password` | MQTT-Passwort | leer |
| `poll_interval_hours` | Abfrageintervall in Stunden (Minimum 1) | `12` |
| `import_statistics` | Monatsverbräuche in die Langzeitstatistik importieren | `true` |

> Vivawest aktualisiert die Werte nur einmal im Monat (meist in den ersten Tagen des
> Folgemonats). Ein Intervall von 12 Stunden reicht völlig aus.

## Sensoren

Alle Sensoren erscheinen unter dem Gerät **Vivawest** und zeigen den Verbrauch
des **letzten abgerechneten Monats**:

| Sensor | Einheit | Beschreibung |
|---|---|---|
| Vivawest Kaltwasser Verbrauch | m³ | Kaltwasser |
| Vivawest Warmwasser Verbrauch | m³ | Warmwasser |
| Vivawest Warmwasser Energie | kWh | Energie für Warmwasser |
| Vivawest Heizenergie | kWh | Heizenergie (vom Portal umgerechnet) |
| Vivawest Heizung Verbrauchseinheiten | VE | Heizung, Rohwert der Heizkostenverteiler |

Attribute: `ablesedatum`, `monat`, `jahr` und, falls vorhanden,
`vergleich_liegenschaft_prozent` (Abweichung vom Durchschnitt des Hauses pro m²).

### Miete und Vorauszahlungen

| Sensor | Einheit | Beschreibung |
|---|---|---|
| Vivawest Miete gesamt | EUR | Monatlicher Gesamtbetrag |
| Vivawest Grundmiete | EUR | Grundmiete |
| Vivawest Vorauszahlung Betriebskosten | EUR | Monatliche Betriebskosten-Vorauszahlung |
| Vivawest Vorauszahlung Heizkosten | EUR | Monatliche Heizkosten-Vorauszahlung |

Attribute: `gueltig_von`, `gueltig_bis` und, wenn bereits eine Änderung geplant ist,
`naechster_betrag` und `naechste_aenderung`. „Miete gesamt“ listet zusätzlich alle
Positionen unter `positionen` auf (z. B. TV-Versorgung).

Kosten pro m³ oder kWh stellt das Portal nicht bereit. Für die Kostenberechnung im
Energie-Dashboard trägst du dort einen festen Preis pro Einheit ein (z. B. aus deiner
letzten Heiz- bzw. Wasserkostenabrechnung).

## Energie-Dashboard

Das Add-on legt diese Statistiken an:

| Statistik | Einheit | Im Energie-Dashboard unter |
|---|---|---|
| `vivawest:kaltwasser` | m³ | Wasserverbrauch |
| `vivawest:warmwasser` | m³ | Wasserverbrauch |
| `vivawest:heizenergie` | kWh | z. B. Einzelgeräte oder Netzverbrauch |
| `vivawest:warmwasser_energie` | kWh | z. B. Einzelgeräte |

Einrichten unter **Einstellungen → Dashboards → Energie**. Die Statistiken erscheinen dort
in der Auswahl als „Vivawest Kaltwasser“ usw.

Jeder Monatsverbrauch wird gleichmäßig auf alle Stunden des Monats verteilt. Tages- und
Wochenwerte sind daher Durchschnittswerte, die Monatssummen stimmen genau. Beim ersten
Start werden alle Monate importiert, die das Portal liefert (ca. 10–12 Monate,
bei der Heizung weniger). Neue Monate kommen automatisch dazu, sobald Vivawest sie
veröffentlicht.

Der Importstatus liegt in `/data/state.json` des Add-ons. Wird das Add-on deinstalliert
und neu installiert, beginnt die Zählung von vorn.

## Fehlerbehebung

Im Tab **Protokoll** des Add-ons:

- `Login und/oder Passwort fehlen`: Zugangsdaten in der Konfiguration eintragen.
- `Vivawest-API-Fehler: POST login → HTTP 403`: E-Mail oder Passwort falsch.
- `Vivawest-API-Fehler: … HTTP 404`: Vivawest hat die API geändert, bitte ein Issue eröffnen.
- `MQTT-Verbindung fehlgeschlagen`: Läuft der Broker? Stimmen Host, Port, Benutzer und Passwort?
- `Statistik-Import fehlgeschlagen`: Die Meldung danach nennt den Grund. Die Sensoren
  werden trotzdem aktualisiert.
- `Keine 'messwerte' in der API-Antwort`: Für das Konto liegen (noch) keine Verbrauchswerte vor.

### Upgrade von Version 1.0.x

Die alten Sensoren hatten `state_class: total_increasing`. Weil sie Monatswerte und keine
Zählerstände zeigen, entstanden dabei falsche Statistiken. Home Assistant meldet nach dem
Update evtl. unter **Entwicklerwerkzeuge → Statistiken** ein Problem für diese Sensoren.
Dort „Beheben“ bzw. die alten Statistiken löschen und im Energie-Dashboard auf die neuen
`vivawest:…`-Statistiken umstellen.
