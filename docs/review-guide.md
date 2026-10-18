# Begutachtung und Bedienung

Die Repository-Fassung verwendet das Dashboard des ursprünglichen Projekts: **Quick review**, **Assessment queue** und **Detailed review**, einschließlich Tabellen, Filtern, Detailansicht, Dark Mode und manueller Auswahl.

```sh
python run_demo.py demo
python run_demo.py dashboard serve
```

Unter http://127.0.0.1:8765/ stehen sechs erfundene Stellen bereit: drei mit detaillierter Bewertung und drei für die Vorauswahl. Bewertungen und Prioritäten sind ausdrücklich simuliert. Die Sammlung, Speicherung, Aufgabenvorbereitung, Validierung und Übernahme verwenden die tatsächlichen Dienste.

1. In **Detailed review** die **Details** einer passenden Stelle öffnen. Bei **Minimum fit** den Wert `0` eingeben, um auch das absichtlich unpassende Beispiel zu sehen.
2. In **Quick review** eine Stelle mit **Queue detailed review** auswählen.
3. In **Assessment queue** die gewünschte Anzahl einstellen, **Preview selection** wählen, die Auswahl prüfen und **Prepare batches** ausführen. Erst der Mensch übergibt anschließend die erzeugte `HANDOFF.md` an einen beliebigen geeigneten Agenten.
4. In **Detailed review** mit **Start application** einen Bewerbungsordner anlegen. Unter `var/applications/01_active/` liegen die Bewertung, ein Arbeitsauftrag und ein Anschreibenentwurf zur gemeinsamen Bearbeitung. Private Unterlagen werden durch fiktive Hinweise ersetzt; nichts wird versendet.

Die vollständige [englische Schrittfolge](walkthrough.md) erklärt außerdem einen echten Agentendurchlauf in einem getrennten Workspace und die separate E-Mail-Demonstration per CLI.

Zum Aktualisieren einer bereits laufenden älteren Fassung: Server mit `Ctrl+C` beenden, `python run_demo.py demo` erneut ausführen, Server starten und die Browserseite neu laden. Bestehende menschliche Entscheidungen bleiben erhalten. Bei offenen vorbereiteten Aufgaben meldet das Demo-Kommando dies; für eine unabhängige Begutachtung kann dann ein neuer Workspace genutzt werden:

```sh
python run_demo.py --workspace portfolio-review demo
python run_demo.py --workspace portfolio-review dashboard serve
```

Die Laufzeitdaten bleiben lokal und gelangen nicht zu GitHub. Der produktive Bewerbungsordner wird nicht verändert.
