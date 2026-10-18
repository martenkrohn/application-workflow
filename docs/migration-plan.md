# Übernahmeplan

Stand: 18. Oktober 2026. Neues lokales Repository, noch ohne GitHub-Verbindung.

## Festgelegter Umgang

Das private Arbeitsprojekt bleibt der Ort für den tatsächlichen Bewerbungsalltag. Diese Fassung ist ein gesondert gepflegter Projektstand für die Präsentation. Änderungen werden gezielt und dateiweise übernommen; es gibt keine automatische Synchronisierung des privaten Ordners. Die frühere Git-Historie wird nicht übernommen.

## Erster Schritt

- Eigenen Projektordner und eine frische Git-Historie anlegen.
- Sechs geprüfte Python-Dateien mit Datenmodellen, Paketinitialisierung und vorhandener Fake-Quelle unverändert übernehmen.
- Ein synthetisches Beispiel ohne Netzwerkzugriffe bereitstellen.
- Einen lokalen Herkunftsnachweis mit Dateihashes und einer ersten Fundstellenliste anlegen; dieser liegt unter `local/` und ist von Git ausgeschlossen.

## Noch zu übernehmen

| Bereich | Vorbereitung |
| --- | --- |
| Quellenabstraktion | Konkrete Domains und Website-Adapter privat halten; Sonderfälle aus gemeinsamer Fachlogik trennen; neutrale Demo-Quelle verwenden |
| SQLite und Sammlung | Migrationen auf persönliche Vorgaben prüfen; mit leerer Demo-Datenbank und Fake-Quelle starten |
| Konfiguration | Persönliche Profilpfade und Kontoeinstellungen durch ausdrücklich fiktive Beispiele ersetzen |
| Bewertungen | Fachlogik, Ergebnisschemata und benötigte Prompts zusammen übernehmen; vorhandene Validierung verwenden |
| Dashboard | Persönliche Beschriftungen und Vorgaben entfernen; nur Demo-Daten anzeigen |
| E-Mail | Persönliche Adressvorgaben abstrahieren; ausschließlich erfundene Testnachrichten mitführen |
| Tests | Geeignete bestehende Tests auswählen; Fixtures einzeln auf echte Inhalte prüfen |
| Dokumentation | Eigenanteil, verwendete Hilfsmittel und Grenzen konkret beschreiben; später kurze englische README ergänzen |

## Persönliche Kopplungen im Arbeitscode

Die erste Sichtung hat persönliche Vorgaben auch im Quellcode gefunden: Standardprofil und Profil-ID, Adressvorgaben für E-Mail/Bewerbungen, Voreinstellungen in der Datenhaltung sowie Dashboard-Beschriftungen. Diese Bereiche werden vor der Übernahme gezielt angepasst. Eine leere Datenbank und eine `.gitignore` allein würden diese Kopplungen nicht entfernen.

Die lokale Musterprüfung liefert nur Prüfhinweise. Ein fehlender Treffer ist keine vollständige Freigabe für die Veröffentlichung.

## Nicht übernehmen

- Echte Bewerbungsordner, Lebensläufe, Zeugnisse, Anschreiben und Unterschriften.
- Persönliche Profile, Bewertungsergebnisse und private Arbeitsanweisungen.
- Echte E-Mails, Anhänge, Nachrichtenexporte und Scraping-Belege.
- Produktionsdatenbanken, Logs, Browserprofile, Cookies und OAuth-Dateien.
- Alte Git-Historie, lokale Editorzustände und maschinenspezifische Betriebsdateien.

## Bevor GitHub eingerichtet wird

Die ausgewählten Dateien und sämtliche Commits dieser neuen Historie prüfen, das synthetische Beispiel zu einem zusammenhängenden Demonstrationsablauf ausbauen, passende bestehende Tests übernehmen und die Lizenzierung festlegen. Derzeit gibt es keinen Remote und keinen automatischen Upload.

## Quellen und öffentliche Darstellung

Die [neutrale Quellenanbindung](source-adapters.md) beschreibt den vorgesehenen Ansatz: allgemeiner Workflow und synthetische Beispiele im öffentlichen Projekt, konkrete Website-Adapter separat. Die vorhandene `SourceAdapter`-Schnittstelle ist zusätzlich übernommen. Domainersetzung allein ersetzt keine Entkopplung der Parser.
