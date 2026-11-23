# Vivawest für Home Assistant

Custom Integration für das [Vivawest Kundenportal](https://kundenportal.vivawest.de).
Sie bringt deine Heizungs- und Wasserverbräuche sowie Miete und Vorauszahlungen nach
Home Assistant, inklusive Langzeitstatistik für das Energie-Dashboard.

> Inoffizielles Projekt, nicht mit der VIVAWEST Wohnen GmbH verbunden. Die Integration
> nutzt die (undokumentierte) Schnittstelle des Kundenportals und kann bei Änderungen
> am Portal zeitweise ausfallen.

## Funktionen

- **Verbräuche des letzten Monats:** Kaltwasser, Warmwasser (m³ und kWh), Heizung (kWh und Verbrauchseinheiten)
- **Energie-Dashboard:** Jeder Monatsverbrauch wird rückwirkend gleichmäßig auf die
  Stunden des jeweiligen Monats verteilt und als Statistik importiert, inklusive aller
  Monate, die das Portal liefert (ca. 10–12)
- **Zählerstände pro Einzelzähler:** jeder Wasserzähler und Heizkostenverteiler mit Raum
- **Miete und Vorauszahlungen:** aktuelle Beträge und die nächste geplante Änderung
- **Nebenkostenabrechnungen:** Nachzahlung oder Guthaben, Kosten, Prognose, neue Vorauszahlung
- **Portal:** ungelesene Nachrichten, offene Schadensmeldungen, nächster Handwerkertermin
- **Einrichtung per UI:** kein YAML, kein MQTT nötig

## Installation

### Über den Marketplace

1. **Einstellungen → Marketplace → ⋮ → Add from link**
2. `https://github.com/leonseemann/ha-vivawest` eintragen und **Vivawest** installieren
3. Home Assistant neu starten

### Manuell

Den Ordner `custom_components/vivawest` in das Verzeichnis `config/custom_components/`
deiner Home-Assistant-Installation kopieren und neu starten.

## Einrichtung

**Einstellungen → Geräte & Dienste → Integration hinzufügen → Vivawest**, dann E-Mail und
Passwort des Kundenportals eingeben. Die Daten werden alle 12 Stunden abgerufen.
Vivawest veröffentlicht neue Werte meist in den ersten Tagen des Folgemonats.

Ändert sich dein Passwort, fordert Home Assistant dich automatisch zur erneuten Anmeldung auf.

## Sensoren

Alle Sensoren gehören zum Gerät **Vivawest**.

| Sensor | Einheit | Inhalt |
|---|---|---|
| Kaltwasser | m³ | Verbrauch des letzten Monats |
| Warmwasser | m³ | Verbrauch des letzten Monats |
| Warmwasser Energie | kWh | Energie für Warmwasser im letzten Monat |
| Heizenergie | kWh | Heizenergie im letzten Monat (vom Portal umgerechnet) |
| Heizung Verbrauchseinheiten | VE | Rohwert der Heizkostenverteiler |
| Miete gesamt | EUR | Monatlicher Gesamtbetrag, Attribut `positionen` listet alle Posten |
| Grundmiete | EUR | |
| Vorauszahlung Betriebskosten | EUR | |
| Vorauszahlung Heizkosten | EUR | |
| TV-Versorgung | EUR | |
| Kaltwasserzähler *Raum* | m³ | Zählerstand eines Einzelzählers |
| Warmwasserzähler *Raum* (+ Energie) | m³ / kWh | Zählerstand eines Einzelzählers |
| Heizkostenverteiler *Raum* | VE | Stand eines Heizkostenverteilers |
| Abrechnung *Art* | EUR | Ergebnis der neuesten Abrechnung je Art (Betriebs-, Heiz-, Wasserkosten): positiv = Nachzahlung, negativ = Guthaben |
| Ungelesene Nachrichten | | Postfach im Portal |
| Neuigkeiten im Portal | | Benachrichtigungszähler des Portals |
| Offene Schadensmeldungen | | Anzahl, Attribut `meldungen` mit Schaden, Status und Termin |
| Nächster Handwerkertermin | Zeitpunkt | Beginn des Zeitfensters, „unbekannt“ ohne Termin |

- **Verbrauchssensoren:** Attribute `ablesedatum`, `monat`, `jahr` und, falls vorhanden,
  `vergleich_liegenschaft_prozent` (Abweichung vom Hausdurchschnitt pro m²).
- **Kostensensoren:** Attribute `gueltig_von`, `gueltig_bis` und bei geplanten Änderungen
  `naechster_betrag` / `naechste_aenderung`.
- **Zählersensoren:** Sie entstehen automatisch für jeden Zähler, den das Portal meldet
  (Zählertausch = neuer Sensor). Attribute `raum`, `zaehlernummer`, `monatsverbrauch`,
  `ablesedatum`. Der Wert ändert sich nur einmal im Monat. Für das Energie-Dashboard sind
  die Statistiken unten besser geeignet, weil sie den Verbrauch auf den Monat verteilen.
- **Abrechnungssensoren:** Attribute `ergebnis` (Nachzahlung/Guthaben), `zeitraum_von/bis`,
  `kosten`, `kosten_vorjahr`, `geleistete_vorauszahlungen`, `prognose`, `neue_vorauszahlung`,
  `positionen` und, falls angegeben, `faellig_am` / `betrag_faellig`.

## Energie-Dashboard

| Statistik | Einheit | Verwendung |
|---|---|---|
| `vivawest:kaltwasser` | m³ | Wasserverbrauch |
| `vivawest:warmwasser` | m³ | Wasserverbrauch |
| `vivawest:heizenergie` | kWh | Energie, z. B. als Einzelgerät |
| `vivawest:warmwasser_energie` | kWh | Energie, z. B. als Einzelgerät |

Einrichten unter **Einstellungen → Dashboards → Energie**. Weil die Monatswerte
gleichmäßig verteilt werden, sind Tages- und Wochenwerte Durchschnittswerte. Die
Monatssummen stimmen genau. Korrigiert Vivawest einen Monat nachträglich, wird er
beim nächsten Abruf neu importiert.

Preise pro m³ oder kWh stellt das Portal nicht bereit. Für die Kostenberechnung trägst du
im Energie-Dashboard einen festen Preis ein, z. B. aus deiner letzten Abrechnung.

## Umstieg vom früheren Add-on

Das Vivawest-Add-on wird nicht mehr weiterentwickelt. So steigst du um:

1. Integration installieren und einrichten (siehe oben).
2. Das Add-on unter **Einstellungen → Add-ons → Vivawest** deinstallieren und das
   Repository `https://github.com/leonseemann/ha-vivawest` aus dem Add-on Store entfernen.
3. Unter **Einstellungen → Geräte & Dienste → MQTT** das alte Gerät **Vivawest** löschen.
   Die alten Sensoren `sensor.vivawest_*` verschwinden damit. Automationen und Dashboards
   gegebenenfalls auf die neuen Sensoren umstellen.

Die Statistiken (`vivawest:kaltwasser` usw.) behalten ihre IDs. Energie-Dashboard und
bisherige Historie laufen ohne Änderung weiter.

## Fehlerbehebung

- **Sensoren „nicht verfügbar“:** Das Portal war beim letzten Abruf nicht erreichbar. Es
  wird automatisch erneut versucht.
- **Diagnosedaten** (persönliche Daten geschwärzt): **Geräte & Dienste → Vivawest → ⋮ →
  Diagnosedaten herunterladen**. Bitte bei Issues anhängen.
- **Debug-Log:** **Geräte & Dienste → Vivawest → ⋮ → Debug-Protokollierung aktivieren**.

## Entwicklung

Siehe [AGENTS.md](AGENTS.md).
