# Architektur

## Bestehendes Arbeitsprojekt

| Schicht | Aufgabe | Stand in dieser Projektfassung |
| --- | --- | --- |
| Domain | Datenmodelle, Qualitäts- und Auswahlregeln | Grundlegende Modelle übernommen |
| Application | Sammlung, Bewertung, E-Mail-Einordnung und Bewerbungsverwaltung | Schnittstellen übernommen; Anwendungslogik noch offen |
| Adapters | Stellenquellen, Browser und Gmail | Nur vorhandene Fake-Quelle übernommen |
| Infrastructure | SQLite, Migrationen, Artefakte, Konfiguration und LLM-Anbindung | Noch zu prüfen und zu übernehmen |
| Web / CLI | Lokales Dashboard und Bedienung | Noch zu prüfen und zu übernehmen |
| Prompts | Versionierte Anweisungen und Ergebnisschemata | Noch zu prüfen und zu übernehmen |

Das erste Beispiel verwendet dieselbe vorhandene Fake-Quelle und dieselben Datenmodelle wie das Arbeitsprojekt. Es ist ein überprüfbarer Einstieg in die Übernahme; es demonstriert noch keine LLM-Bewertung oder E-Mail-Verarbeitung.

## Geplanter Demonstrationsablauf

1. Synthetische Stellenanzeigen über eine lokale Quelle einlesen.
2. Belege normalisieren und in einer ausschließlich für die Demo angelegten SQLite-Datenbank speichern.
3. Ein fiktives Profil und eine menschliche Auswahl verwenden, daraus ein begrenztes Agenten-Arbeitspaket vorbereiten und strukturierte Beispielergebnisse ohne erforderliche Live-Anmeldung validieren.
4. Ergebnisse im übernommenen lokalen Dashboard anzeigen.
5. Die E-Mail-Klassifikation mit vollständig erfundenen Nachrichten ergänzen.

Die vorhandenen Fachmodule sollen nachvollziehbar übernommen werden. Eine unabhängige Neuentwicklung des gesamten Systems ist nicht vorgesehen.

## Quellenabstraktion

Der gemeinsame Workflow soll konkrete Website-Namen nicht kennen. Die bestehende Adapter-Schnittstelle ist übernommen; die Trennung der weiteren produktiven Sonderfälle ist geplant. Details und Grenzen stehen unter [Neutrale Quellenanbindung](source-adapters.md).

## Mensch und KI-Agent als Teil des Ablaufs

Neben den Softwareschichten sind die Übergaben zwischen Mensch, KI-Agent und Anwendung zentral. Der Mensch legt Auswahl und Umfang fest; der Agent bewertet vorbereitete Inhalte; die Anwendung validiert und speichert die Ergebnisse. Die derzeitige konkrete Agentenanbindung und die geplante neutrale Demonstration sind unter [Zusammenarbeit zwischen Mensch, KI-Agent und Anwendung](human-ai-workflow.md) voneinander abgegrenzt.
