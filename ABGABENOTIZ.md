# Finn · Abgabenotiz zur Case Study

**Verträge einmal fachlich prüfen.** Ich trenne Vertragsfreigabe und Wirtschaftsplanprüfung, weil die Vertragsregeln die gemeinsame Grundlage für viele Pläne sind. Die KI schlägt Regeln mit Fundstellen vor. Finn prüft die Belege am PDF. Nach der fachlichen Freigabe liegt die unterstützte Regel versioniert in DuckDB. So bleibt nachvollziehbar, nach welcher Definition und Grenze jeder Plan geprüft wurde. Eine geänderte Vertragsfassung braucht eine neue Prüfung. Die vier Originalverträge sind als geprüfter Startbestand vorbereitet.

**Jeden Wirtschaftsplan neu prüfen.** Die Person, die den Plan einreicht, braucht nur die aktuelle Excel-Datei. Python liest die Planwerte und berechnet Kennzahlen, Rundung und Puffer nach den freigegebenen Regeln. Mangels separatem Ausweis setzt Finn außerordentliche EBITDA-Bereinigungen im Beispiel mit null an. Bei widersprüchlichen Angaben bleibt die betroffene Kennzahl offen. Die Antwort im ursprünglichen Mailverlauf bleibt kompakt. Vertragsstellen, Excel-Zellen und Rechenwege stehen im angehängten PDF.

**KI bei Planung und Entwicklung.** In der Planung habe ich KI genutzt, um die Aufgabenstellung zu strukturieren und Optionen für Vertragsfreigabe, Datenspeicherung und Mailablauf durchzuspielen. Bei der Umsetzung hat sie beim Schreiben und Überarbeiten des Python-Codes, bei der Fehlersuche und beim Entwerfen von Testfällen geholfen, etwa für umbenannte Excel-Spalten und temporäre Versandfehler.

**KI im laufenden Finn.** Bei der Vertragsprüfung erhält das Modell den PDF-Text und schlägt Kennzahlen, Grenzen und Fundstellen vor. In der Planprüfung per Mail kann es zusätzlich kontrollieren, ob Python die richtige Zeile, das richtige Jahr und die richtige Einheit zugeordnet hat. Belegte Zuordnungszweifel erscheinen im Prüfbericht und lassen die betroffenen Kennzahlen offen. Die Planprüfung funktioniert auch ohne diesen Abgleich mit der Zuordnung aus Python.

**Sicherheit am Mail-Eingang.** Die lokale Freigabeliste begrenzt, wer einen Auftrag auslösen kann. Finn prüft zuerst die Kopfzeilen. Ohne Freigabe sendet er nur einen Hinweis und lädt weder Nachrichtentext noch Anhänge. Vertragsentwürfe kann nur derselbe weiterhin freigegebene Absender bestätigen.

**Stand im Mailtest.** Der vollständige Ablauf bis zur Ergebnis-Mail wurde erfolgreich getestet. Vereinzelt fehlte trotz Annahme durch Microsoft Graph eine Antwort im Zielpostfach. Bei den anschließenden Tests mit verschiedenen Mailanbietern und Versandwegen kamen die Antworten an.

**Mit mehr Zeit.** Ich würde getrennte Rechte für das Einreichen von Wirtschaftsplänen, den Zugriff auf Pläne und Prüfhistorie sowie das Ändern und Freigeben von Vertragsregeln einführen. Dazu käme eine serverseitige Prüfung der Absenderidentität. Finn würde als Dienst auf einem Server laufen und neue Aufträge auch bei ausgeschaltetem Arbeitsplatzrechner abrufen. Für den gemeinsamen Zugriff mehrerer Personen würde ich den Wechsel von DuckDB zu PostgreSQL prüfen. Weitere Ausbauschritte wären eine beleggestützte KI-Zuordnung für frei gestaltete Excel-Dateien, zusätzliche fachlich freigegebene Vertragsformeln sowie die automatische Auswahl gültiger Regeln nach Stichtag und Vertragsnachträgen.

[Zeitlog](ZEITLOG.md) · [README mit Schnellstart](README.md)
