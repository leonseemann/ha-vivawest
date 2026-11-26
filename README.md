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
| Prognose Betriebskosten | EUR | Erwartete Nachzahlung (positiv) oder Guthaben (negativ) der nächsten Betriebskostenabrechnung |
| Prognose Heizkosten | EUR | Dasselbe für die Heizkosten |
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
- **Prognosesensoren:** Schätzung für den laufenden Abrechnungszeitraum (das Jahr nach der
  neuesten Abrechnung):
  `Ergebnis = erwartete Kosten − Vorauszahlungen im Zeitraum`.
  - *Erwartete Kosten:* die Prognose von Vivawest aus der letzten Abrechnung, sonst deren
    Kosten. Der verbrauchsabhängige Teil wird mit dem eigenen Verbrauch im Vorjahresvergleich
    (dieselben Monate) hochgerechnet: bei den Heizkosten 70 % (Heizung + Warmwasser in kWh),
    bei den Betriebskosten die Wasserpositionen (Kalt- + Warmwasser in m³). Wasserkosten mit
    eigener Abrechnung zählen zu den Betriebskosten.
  - *Vorauszahlungen:* für jeden Monat der dann gültige Betrag laut Mietkonditionen,
    inklusive angekündigter Änderungen.
  - Der Vorjahresvergleich braucht Vorjahresmonate. Das Portal liefert nur ca. 12 Monate,
    ältere kommen aus der Langzeitstatistik der Integration. Bis genug Daten da sind, gilt
    die Kostenbasis unverändert (`verglichene_monate: 0`).
  - Attribute: `ergebnis`, `zeitraum_von/bis`, `erwartete_kosten`, `kostenbasis`,
    `kostenbasis_quelle` (`prognose`/`vorjahr`), `vorauszahlungen_gesamt`,
    `vorauszahlungen_bisher`, `kostendeckende_vorauszahlung` (Monatsbetrag, der die
    erwarteten Kosten genau deckt), `verglichene_monate`, `verbrauch_vorjahr_prozent`.
  - Preisänderungen beim Versorger kennt die Integration nur, soweit sie in der Prognose von
    Vivawest stecken. Das Ergebnis ist eine Schätzung, keine Abrechnung.

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

### Kosten im Energie-Dashboard

Für importierte Statistiken bietet das Energie-Dashboard **keinen festen Preis** an (die
Optionen sind ausgegraut). Deshalb importiert die Integration die Kosten selbst:

1. **Geräte & Dienste → Vivawest → Konfigurieren:** Preise eintragen (leer = keine Kosten).
   - **Wasserpreis** (€/m³, gilt für Kalt- und Warmwasser): Wasserversorgung + Entwässerung
     aus der Betriebskostenabrechnung, jeweils Gesamtkosten ÷ Gesamtverbrauch des Abrechnungskreises
   - **Preis Warmwasser-Energie** und **Preis Heizenergie** (€/kWh): aus der Heizkostenabrechnung
2. Es entstehen die Statistiken `vivawest:kaltwasser_kosten`, `vivawest:warmwasser_kosten`,
   `vivawest:warmwasser_energie_kosten` und `vivawest:heizenergie_kosten`.
3. Im Energie-Dashboard beim jeweiligen Verbrauch **„Eine Entität verwenden, die die
   Gesamtkosten erfasst“** wählen und die passende Kostenstatistik auswählen.

Eine Preisänderung berechnet alle Monate neu, die das Portal liefert (ca. 12). Fixkosten wie
Zählermiete sind nicht enthalten.

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
