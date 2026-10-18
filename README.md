# Application Workflow

Ein lokaler Bewerbungsworkflow für das Sammeln von Stellenanzeigen, LLM-gestützte Bewertungen und die Verwaltung von Bewerbungsständen.

**Stand: Vorbereitung der Projektveröffentlichung.** Dieses Repository wird schrittweise aus einem bestehenden privaten Arbeitsprojekt aufgebaut. Es enthält zunächst die grundlegenden Datenmodelle und eine vollständig synthetische Stellenquelle. Der vollständige Workflow und das Dashboard sind hier noch nicht übernommen.

## Zielbild

Das bestehende System verbindet Quellenadapter, nachvollziehbare Stelleninformationen, regelbasierte Vorauswahl, validierte LLM-Ergebnisse, SQLite und ein lokales Dashboard. Hinzu kommen die Einordnung von Bewerbungs-E-Mails und die Verwaltung von Bewerbungsständen. Die Projektfassung soll diese Architektur später mit erfundenen Beispielen demonstrieren.

## Bereits enthalten

- Unverändert übernommene Datenmodelle für Quelleneinträge, Belege und Verarbeitungsergebnisse.
- Eine vorhandene Fake-Quelle, die eine erfundene Stellenanzeige erzeugt.
- Ein ausführbares Beispiel ohne Netzwerkzugriff, Anmeldung oder API-Schlüssel.
- Dokumentation zur Architektur und zur weiteren Übernahme.

## Beispiel ausführen

Voraussetzung: Python 3.13. Im Projektordner:

```sh
python examples/preview_vacancy.py
```

Das Beispiel gibt einen erfundenen Stelleneintrag als JSON aus. Die Domain `example.invalid` kennzeichnet die Beispieldaten; es werden keine Stellenportale oder E-Mail-Konten aufgerufen. Eine Paketinstallation ist für dieses Beispiel nicht nötig.

## Aufbau

| Pfad | Inhalt |
| --- | --- |
| `src/job_system/domain/` | Übernommene Datenmodelle |
| `src/job_system/adapters/sources/fake.py` | Vorhandene synthetische Quelle |
| `examples/` | Ausführbare Beispiele |
| `docs/architecture.md` | Architektur und Stand der Übernahme |
| `docs/migration-plan.md` | Nächste Schritte und Abgrenzung zum privaten Arbeitsprojekt |

Der interne Paketname `job_system` bleibt erhalten, damit weitere geprüfte Originalmodule schrittweise übernommen werden können.

## Aktueller Umfang

Echte Bewerbungen, Lebensläufe, persönliche Profile, E-Mails, Datenbanken, Zugangsdaten und Browserzustände werden nicht mitgeführt. Produktive Integrationen sind kein Bestandteil dieses ersten Übernahmeschritts.

Weitere Schritte: [Übernahmeplan](docs/migration-plan.md). Ein GitHub-Repository und eine öffentliche Veröffentlichung sind noch nicht eingerichtet. Eine Lizenz für die spätere Veröffentlichung wurde noch nicht festgelegt.
