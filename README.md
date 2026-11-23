# Vivawest Home Assistant Add-on

Home-Assistant-Add-on-Repository, das Heizungs- und Wasserverbräuche
(Heizenergie, Kaltwasser, Warmwasser) aus dem Vivawest Kundenportal ausliest. Die
Werte erscheinen als MQTT-Sensoren und als Langzeitstatistik für das
Energie-Dashboard. Die Monatswerte werden dabei rückwirkend auf den jeweiligen
Monat verteilt.

[![Repository hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fleonseemann%2Fha-vivawest)

## Add-ons

| Add-on | Beschreibung |
|---|---|
| [Vivawest](vivawest/) | Liest Messwerte aus dem Kundenportal und veröffentlicht sie über MQTT |

## Schnellstart

1. Repository `https://github.com/leonseemann/ha-vivawest` im Add-on Store hinzufügen.
2. Mosquitto-Broker und MQTT-Integration einrichten.
3. Add-on **Vivawest** installieren, Kundenportal-Zugangsdaten eintragen, starten.

Ausführliche Dokumentation: [vivawest/DOCS.md](vivawest/DOCS.md)
Hinweise für Entwickler und KI-Agenten: [AGENTS.md](AGENTS.md)

## Funktionsweise

```
Vivawest Kundenportal API ──(HTTPS, stündlich+)──▶ Add-on (Python) ──(MQTT)──▶ Mosquitto ──▶ Home Assistant
```

## Hinweis

Inoffizielles Projekt, nicht mit der VIVAWEST Wohnen GmbH verbunden.
