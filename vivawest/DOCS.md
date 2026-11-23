# Vivawest Add-on

Dieses Add-on liest deine Wasser-Verbrauchswerte aus dem
[Vivawest Kundenportal](https://kundenportal.vivawest.de) und stellt sie in
Home Assistant als Sensoren bereit.

## Voraussetzungen

- Ein Konto im Vivawest Kundenportal mit freigeschalteter Verbrauchsinformation (UVI).
- Ein MQTT-Broker, z. B. das offizielle **Mosquitto broker** Add-on.
- Die **MQTT-Integration** in Home Assistant (wird bei Mosquitto meist automatisch erkannt).

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
| `poll_interval_hours` | Abfrageintervall in Stunden (Minimum 1) | `1` |

Beispiel:

```yaml
login: mail@example.com
password: geheim
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_user: mqtt
mqtt_password: geheim
poll_interval_hours: 6
```

> Tipp: Die Werte im Portal werden in der Regel nur monatlich aktualisiert –
> ein Intervall von mehreren Stunden reicht völlig aus.

## Sensoren

Alle Sensoren erscheinen unter dem Gerät **Vivawest**:

| Entität | Einheit | Beschreibung |
|---|---|---|
| `sensor.vivawest_kaltwasser_verbrauch` | m³ | Kaltwasserverbrauch |
| `sensor.vivawest_warmwasser_verbrauch` | m³ | Warmwasserverbrauch |
| `sensor.vivawest_warmwasser_energie` | kWh | Energie für Warmwasser |

Jeder Sensor hat die Attribute `ablesedatum`, `monat` und `jahr`.
Die Sensoren haben `state_class: total_increasing` und können im
**Energie-Dashboard** (Wasser bzw. Energie) verwendet werden.

Ist das Add-on gestoppt oder abgestürzt, werden die Sensoren als
„Nicht verfügbar" angezeigt.

## Fehlerbehebung

Im Tab **Protokoll** des Add-ons:

- `Login und/oder Passwort fehlen` – Zugangsdaten in der Konfiguration eintragen.
- `HTTP-Fehler: 401 …` – Zugangsdaten im Kundenportal prüfen.
- `MQTT-Verbindung fehlgeschlagen` – läuft der Broker? Stimmen Host, Port, Benutzer und Passwort?
- `Keine 'messwerte' in der API-Antwort` – für das Konto liegen (noch) keine Verbrauchswerte vor.
