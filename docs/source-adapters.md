# Neutrale Quellenanbindung

## Ziel

Die öffentliche Projektfassung soll den allgemeinen Bewerbungsworkflow zeigen, ohne konkrete produktive Stellenportale zu benennen oder deren private Konfiguration zu veröffentlichen. Zusätzliche Stellenquellen werden über eigene Adapter angebunden. Ein Austausch der Domain allein macht einen Parser nicht für beliebige Websites geeignet.

## Ausgangspunkt im vorhandenen Code

Die bestehende Schnittstelle `SourceAdapter` wurde unverändert nach `src/job_system/application/ports.py` übernommen. Sie verlangt einen Namen und `collect()`, das `SourceItem`-Einträge liefert. Die vorhandene Fake-Quelle erfüllt diese Struktur.

`SourceItem` enthält Rohinhalt und Belege. Es garantiert noch keine einheitliche Stellenbeschreibung: Auswertung des Quellformats und Normalisierung sind zusätzliche Schritte. Diese Unterscheidung bleibt bei der Übernahme erhalten.

Im Arbeitsprojekt sind anbieterspezifische Annahmen derzeit auch außerhalb der Adapter vorhanden, insbesondere bei Datenbereinigung, Quellenregistrierung, Auswahl und Nachladen von Beschreibungen. Diese Teile benötigen vor der Übernahme eine Trennung.

## Vorgesehene Aufteilung

| Bereich | Öffentliche Projektfassung | Private Quellenanbindung |
| --- | --- | --- |
| Gemeinsamer Workflow | Speicherung, Status, Validierung, Bewertung, Dashboard | Keine Kenntnis konkreter Portalnamen |
| Quellenvertrag | Vorhandene Adapter-Schnittstelle und dokumentierte Ein-/Ausgabe | Anbieter liefert Rohbelege über diese Schnittstelle |
| Parsing und Normalisierung | Neutrale gemeinsame Datenstruktur, allgemeine Qualitätsprüfungen | URL-Identitäten, spezielle HTML-/JSON-/Mailformate und Metadatenregeln |
| Konfiguration | Fiktive Beispiele und erläuterte Parameter | Tatsächliche Domains, erlaubte Hosts, Einstiegspunkte und Quellenvorgaben |
| Demo und Tests | Selbst erstellte HTML-/JSON-/E-Mail-Beispiele mit `example.invalid` | Keine echten Anzeigen, Trackinglinks oder Provider-Antworten |

Quellenspezifische Logik wird vor der Übernahme aus gemeinsamen Fachmodulen herausgelöst. Eine Auswahlentscheidung sollte beispielsweise auf allgemeinen Merkmalen eines Datensatzes beruhen, statt einen Portalnamen abzufragen. Ob sich ein Sonderfall durch ein Merkmal ausdrücken lässt oder in einen Adapter gehört, wird am jeweiligen Code geprüft.

## Konfigurierbare Angaben und eigener Programmcode

Domains, Einstiegsadressen, erlaubte Hosts und Abruflimits können grundsätzlich als Konfiguration getrennt werden. CSS-Selektoren sind für einfache Seiten ebenfalls konfigurierbar. Komplexere Navigation, unterschiedliche API-Antworten, Stellen-IDs, E-Mail-Karten und besondere Nachladeschritte brauchen dagegen passende Adapterlogik und Tests.

Die geplante Flexibilität bedeutet deshalb: Weitere Websites lassen sich durch passende Adapter ergänzen. Eine universelle Extraktion durch bloße Eingabe einer beliebigen URL ist nicht implementiert und wird nicht versprochen.

Bestehende Host-Prüfungen, Abruflimits und das Anhalten an Anmelde- oder Verifikationsseiten bleiben fachlich erhalten. Ihre konkreten Quellenparameter gehören in die jeweils passende Konfiguration.

## Reihenfolge der Umsetzung

1. Sammlung und Speicherung mit vorhandener Fake-Quelle übernehmen.
2. Einen selbst erstellten lokalen HTML-Beispielbestand samt kleinem Demo-Parser ergänzen.
3. Einen gemeinsamen Übergang von Rohbelegen zu geprüften Stelleninformationen festlegen; vorhandene Qualitätsregeln gezielt übernehmen.
4. Bewertung und Dashboard ausschließlich mit neutralen Quelldaten verbinden.
5. Produktive Website-Adapter außerhalb der öffentlichen Projektfassung belassen und deren Anschluss an die Schnittstelle dokumentieren.
6. Vor Veröffentlichung auch Dateinamen, Fehlermeldungen, Tests, Fixtures, Konfigurationsbeispiele und Git-Historie auf Anbieterbezüge prüfen.

## Aktueller Umsetzungsstand

Übernommen sind die bereits vorhandene Schnittstelle, die Datenmodelle und die Fake-Quelle. Ein dynamischer Plugin-Loader, ein konfigurierbarer HTML-Scraper und die Entkopplung sämtlicher produktiver Sonderfälle sind noch nicht implementiert.
