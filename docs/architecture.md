# Architektur

## Bestehendes Arbeitsprojekt

| Schicht | Aufgabe | Stand in dieser Projektfassung |
| --- | --- | --- |
| Domain | Datenmodelle, Qualitäts- und Auswahlregeln | Grundlegende Modelle übernommen |
| Application | Sammlung, Bewertung, E-Mail-Einordnung und Bewerbungsverwaltung | Noch zu prüfen und zu übernehmen |
| Adapters | Stellenquellen, Browser und Gmail | Nur vorhandene Fake-Quelle übernommen |
| Infrastructure | SQLite, Migrationen, Artefakte, Konfiguration und LLM-Anbindung | Noch zu prüfen und zu übernehmen |
| Web / CLI | Lokales Dashboard und Bedienung | Noch zu prüfen und zu übernehmen |
| Prompts | Versionierte Anweisungen und Ergebnisschemata | Noch zu prüfen und zu übernehmen |

Das erste Beispiel verwendet dieselbe vorhandene Fake-Quelle und dieselben Datenmodelle wie das Arbeitsprojekt. Es ist ein überprüfbarer Einstieg in die Übernahme; es demonstriert noch keine LLM-Bewertung oder E-Mail-Verarbeitung.

## Geplanter Demonstrationsablauf

1. Synthetische Stellenanzeigen über eine lokale Quelle einlesen.
2. Belege normalisieren und in einer ausschließlich für die Demo angelegten SQLite-Datenbank speichern.
3. Erfundene Profil- und Bewertungsdaten verwenden; LLM-Ausgaben ohne erforderliche Live-Anmeldung demonstrieren und validieren.
4. Ergebnisse im übernommenen lokalen Dashboard anzeigen.
5. Die E-Mail-Klassifikation mit vollständig erfundenen Nachrichten ergänzen.

Die vorhandenen Fachmodule sollen nachvollziehbar übernommen werden. Eine unabhängige Neuentwicklung des gesamten Systems ist nicht vorgesehen.
