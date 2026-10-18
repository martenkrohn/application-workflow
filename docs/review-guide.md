# Begutachtung und Bedienung

## 1. Fertigen Beispieldurchlauf ansehen

Im Repository-Hauptordner mit Python 3.13:

```sh
python run_demo.py demo
python run_demo.py dashboard serve
```

Unter http://127.0.0.1:8765/ erscheinen drei erfundene Stellenanzeigen. Die Bewertungen und E-Mail-Klassifikationen sind ausdrücklich simuliert; kein Modell wurde ausgeführt. Die Sammlung, Speicherung, Vorbereitung, Validierung und Übernahme laufen dagegen tatsächlich durch den übernommenen Anwendungskern.

Im Dashboard lassen sich Quellbelege und Bewertungsbezüge aufklappen. Ein manuell veränderter Bewerbungsstatus bleibt nach erneuter Sammlung erhalten. Ein Statuswechsel versendet keine Bewerbung.

## 2. Übergabe an einen echten Agenten ausprobieren

Der Beispieldurchlauf hat bereits Bewertungen. Für eine neue, unbewertete Auswahl deshalb einen unabhängigen Datenbestand verwenden:

```sh
python run_demo.py --workspace review collect
python run_demo.py --workspace review dashboard serve
```

Die bisherigen Demo-Daten bleiben erhalten. Im neuen Dashboard eine oder mehrere Stellen auswählen und **Prepare selected** anklicken. Der Tab **Agent handoffs** zeigt den Pfad zum Manifest und die Befehle. Im gleichen Ordner wie das Manifest liegt `HANDOFF.md`.

Alternativ ist eine genaue Auswahl über die CLI möglich:

```sh
python run_demo.py --workspace review assessment prepare --ids demo-html:research-analyst
```

Den erzeugten Arbeitsauftrag in einem Agenten-Chat öffnen, dessen Arbeitsverzeichnis dieses Repository ist. Der Agent liest das Manifest, das gemeinsame fiktive Profil, die ausgewählten Stellen sowie die versionierte Anweisung und schreibt JSONL an den angegebenen Ausgabeort. Vorhandene Eingaben bleiben unverändert. Nicht jede Agentenumgebung hat Dateizugriff oder kann CLI-Befehle ausführen; diese Fähigkeiten muss die gewählte Umgebung bereitstellen.

Die konkrete Batch-ID steht in der Ausgabe. Mit dieser ID:

```sh
python run_demo.py --workspace review assessment instructions BATCH_ID
python run_demo.py --workspace review assessment validate BATCH_ID
python run_demo.py --workspace review assessment finish BATCH_ID
```

`BATCH_ID` ersetzen. Die letzten beiden Schritte sind auch über **Validate output** und **Import validated output** möglich. Fehlerhafte oder unvollständige Ergebnisse werden in dieser Fassung nicht importiert. Der Agent kann seine Ausgabe korrigieren; danach erneut validieren. Die Importfunktion startet keinen weiteren Auftrag.

Ohne Agentenkonto lässt sich für die drei eingebauten Beispielstellen eine Ausgabe simulieren:

```sh
python run_demo.py --workspace review agent example-results BATCH_ID
```

Das ist keine Modellbewertung. Eine vorhandene Ergebnisdatei wird dabei nicht überschrieben. Für einen weiteren unabhängigen Versuch einen neuen Namen wie `--workspace review-two` verwenden; Daten müssen nicht gelöscht werden.

## 3. Fiktive E-Mails prüfen

```sh
python run_demo.py --workspace mail-review mail prepare
```

Das erzeugte Manifest unter `var/workspaces/mail-review/mail-handoff/manifest.json` und `prompts/mail-review.md` an den Agenten übergeben. Das Manifest benennt den Ausgabeort im selben Workspace. Nach seiner Bearbeitung:

```sh
python run_demo.py --workspace mail-review mail finish
python run_demo.py --workspace mail-review dashboard serve
```

Nur Klassifikationsvorschläge werden gespeichert. Es gibt weder einen Mailboxzugriff noch automatisch angewendete Bewerbungsstatusänderungen. Der voreingestellte Demo-Durchlauf erledigt diesen Vorgang mit gekennzeichneten Beispielausgaben.

## 4. Was für die Begutachtung wichtig ist

- Ist klar, welche Entscheidung beim Menschen liegt?
- Sind Auswahl, vorbereitete Aufgaben und ihre Eingaben nachvollziehbar?
- Sind echte Agentenausgaben von simulierten Beispielen unterscheidbar?
- Lassen sich Quellen, Belegverweise und Validierungsfehler überprüfen?
- Beschreibt die README den adaptierten Projektumfang ehrlich?

Die Repository-Fassung arbeitet unabhängig vom produktiven Bewerbungsordner. Ihre Laufzeitdaten bleiben lokal und sind nicht Teil des GitHub-Uploads.
