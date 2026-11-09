# Finn · Covenant-Prüfung per E-Mail

Finn prüft den Wirtschaftsplan der fiktiven Stadtwerke Musterstadt gegen vier Kreditverträge. Auf eine Auftragsmail antwortet er im selben Verlauf mit Planwerten, Vertragsgrenzen und dem jeweiligen Puffer. Der angehängte PDF-Prüfbericht enthält Rechenwege, Vertragszitate und Excel-Fundstellen.

Python berechnet Kennzahlen, Rundung und Grenzabstände. Die KI liest Vertragsklauseln und kann die Zuordnung der Excel-Felder prüfen. Finn übernimmt geprüfte Vertragsregeln in DuckDB und verwendet sie bei späteren Planprüfungen erneut.

[Lokaler Schnellstart](#lokaler-schnellstart) · [Vertragsfreigabe und Planprüfung](#warum-vertragsfreigabe-und-planprüfung-getrennt-sind) · [Outlook einrichten](#outlook-verbindung-testen) · [Tests](#robustheit-und-regressionstests) · [Abgabenotiz](ABGABENOTIZ.md) · [Zeitlog](ZEITLOG.md)

## Lokaler Schnellstart

Für die Demo mit dem geprüften Vertragsbestand genügen Python 3.12 und die Projektpakete. Der Lauf funktioniert ohne Outlook-Konto oder KI-Key.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python finn.py demo
```

Unter Windows in PowerShell die Umgebung mit `py -3.12 -m venv .venv` erstellen. Danach für Installation und Ausführung `.venv\Scripts\python.exe` verwenden.

Finn prüft die mitgelieferte [Original-Excel](beispiele/Wirtschaftsplan_2027.xlsx) und gibt die Pfade zur Mailvorschau `antwort.html` und zum `pruefbericht.pdf` aus. Die Dateien liegen unter `output/<Prüflauf-ID>/`. Beim ersten Lauf übernimmt Finn die geprüften Verträge und Regeln aus `fixtures/` in `output/finn.duckdb`.

### Erwartetes Ergebnis für den Originalplan

Stichtag ist der 31.12.2027. Alle vier geprüften Financial Covenants werden auf Planbasis eingehalten.

| Vertrag | Kennzahl | Planwert | Vertragsgrenze | Puffer |
|---|---|---:|---:|---:|
| Förderbank | Wirtschaftliche Eigenkapitalquote | 34,32 % | ≥ 27,50 % | 6,82 Prozentpunkte |
| Landesbank | Schuldendienstdeckungsgrad (DSCR) | 1,56× | ≥ 1,30× | 0,26× |
| Konsortialkredit | Bilanzielle Eigenkapitalquote | 30,7 % | ≥ 30,0 % | 0,7 Prozentpunkte |
| Konsortialkredit | Dynamischer Verschuldungsgrad | 3,37× | ≤ 3,50× | 0,13× |

Der Sparkassenvertrag enthält keine verbindliche Kennzahlengrenze.

Der zweite Beispielplan hat umbenannte Tabellenblätter, verschobene Spalten und zwei Grenzverletzungen.

```sh
python finn.py excel beispiele/31-Wirtschaftsplan-2027-Alternatives-Layout.xlsx
```

In diesem Lauf liegen der DSCR bei **1,21×** und die bilanzielle Eigenkapitalquote bei **29,3 %**. Für eigene Pläne den Pfad zur Excel-Datei an `finn.py excel` übergeben.

## Warum Vertragsfreigabe und Planprüfung getrennt sind

**Verträge sind die Prüfgrundlage.** Beim Einlesen eines Vertrags schlägt die KI Kennzahlen, Definitionen und Grenzen mit Fundstellen vor. Finn prüft die Belege am PDF. Ein Mensch prüft den Entwurf fachlich, bevor eine unterstützte Regel aktiviert wird. Die freigegebene Vertrags- und Regelversion wird in DuckDB gespeichert. Neue oder geänderte Vertragsfassungen gehen wieder durch die Vertragsprüfung.

**Wirtschaftspläne sind die laufenden Aufträge.** Wer einen Plan prüfen lassen will, schickt nur die aktuelle Excel-Datei. Finn liest die Planwerte bei jedem Auftrag neu und berechnet die Kennzahlen anhand der aktiven Regeln. Dieselbe Vertragsfassung wird einmal fachlich beurteilt. So können zwei Planprüfungen auf derselben Grundlage verglichen werden, und jeder Prüflauf bleibt seiner Vertragsversion zugeordnet. Eine erneute KI-Auslegung des Vertrags könnte dagegen Grenzwerte oder Definitionen zwischen zwei Läufen verändern.

## Wo KI eingesetzt wird

Bei der Planung habe ich KI genutzt, um die Anforderungen zu strukturieren und Architekturentscheidungen wie die Trennung von Vertragsfreigabe, Planprüfung und Mailversand durchzuspielen. Bei der Entwicklung half sie beim Schreiben und Überarbeiten des Codes, bei der Fehlersuche und beim Entwurf von Testfällen mit abweichenden Excel-Layouts, missverständlichen Vertragsklauseln und temporären Versandfehlern.

| Schritt im laufenden Finn | Aufgabe der KI | Aufgabe von Python |
|---|---|---|
| Vertragsanalyse per Mail oder mit `finn.py extract` | Vertragsklauseln einordnen und Kennzahl, Definition, Grenze und Fundstelle vorschlagen | PDF-Text seitenweise extrahieren und die vorgeschlagenen Belege am Vertrag prüfen |
| Wirtschaftsplan per Mail | Bei eingerichteter API die von Python gefundene Zuordnung anhand von Zeilenname, Planjahr und Einheit prüfen | Excel lesen, Plausibilitätsprüfungen durchführen und belegte KI-Hinweise im Prüfbericht ausweisen |
| Berechnung und Ausgabe | – | Kennzahlen nach den freigegebenen Regeln berechnen, runden, mit den Grenzen vergleichen und Antwort samt Prüfbericht erzeugen |

Die lokalen Befehle `demo` und `excel` laufen mit dem geprüften Vertragsbestand ohne KI-API.

## Vertragsanalyse mit KI

Die Vorlage `.env.example` als `.env` kopieren. In der lokalen Datei `FINN_API_URL` mit dem vollständigen HTTPS-Endpunkt einer Chat-Completions-kompatiblen API, `FINN_API_MODEL` und `FINN_API_KEY` setzen. Auf dem Mac lässt sich die versteckte Datei im Projektordner mit `open -e .env` öffnen.

```sh
python finn.py extract fixtures/Vertrag_4_Konsortialkredit.pdf
```

Für die Vertragsdemo liest Finn das PDF seitenweise mit pypdf und übergibt den Text an die konfigurierte API. Die Ausgabe `output/Vertrag_4_Konsortialkredit.review.json` enthält die neu erkannten Regeln mit geprüften PDF-Fundstellen. Im Konsortialkredit sollten die bilanzielle Eigenkapitalquote von mindestens 30,0 % und der dynamische Verschuldungsgrad von höchstens 3,50× erscheinen. Ohne PDF-Argument verarbeitet `python finn.py extract` alle vier Originalverträge. Dieser Befehl speichert Prüfentwürfe. Die Aktivierung für spätere Wirtschaftsplanprüfungen lässt sich getrennt per Mail im Testbestand vorführen.

Die KI-Ausgabe ist eine Vorlage für die fachliche Prüfung. Die geprüften Startregeln in `fixtures/seed-rules.json` werden im Normalmodus einmalig in DuckDB übernommen. Danach liest Finn die aktiven Verträge und Regeln ausschließlich aus DuckDB. Die Startdatei bleibt zur reproduzierbaren Einrichtung und zum Abgleich der bekannten Originalverträge im Repo. Formelzuordnung und Rechenlogik sind in `calculation.py` explizit implementiert. Ein weiterer Vertrag mit einer anderen Formel braucht eine fachliche Prüfung und gegebenenfalls eine Erweiterung des Rechenkerns. Der aktuelle Regelsatz ist für den Planstichtag 31.12.2027 der Case Study geprüft. Eine automatische Auswahl der Regeln nach Laufzeit oder Vertragsnachträgen ist ein späterer Schritt.

## Prüffälle und Annahmen

- Die wirtschaftliche Eigenkapitalquote wird ungerundet mit der Grenze verglichen und mit zwei Dezimalstellen angezeigt. Für eingeforderte, ausstehende Einlagen setzt Finn mangels Ausweis im Beispiel null an.
- Im DSCR gehen nachrangige Gesellschafterdarlehen in den Schuldendienst ein. Finn gleicht die GuV-Zinsen mit dem Darlehensspiegel ab.
- Beim dynamischen Verschuldungsgrad entsprechen die Finanzschulden des Beispiels ohne nachrangiges Gesellschafterdarlehen den Bankverbindlichkeiten. Weitere anrechenbare Finanzschulden würden eine zusätzliche Planposition benötigen.
- Beide EBITDA-Berechnungen setzen mangels separaten Ausweises derartiger Positionen null für außerordentliche Bereinigungen an.
- Die Mail zeigt offene Prüfungen zuerst, dann Verstöße und eingehaltene Regeln nach relativem Abstand zur Grenze. Der Puffer wird in der Einheit der Kennzahl ausgegeben.
- Excel-Zellbelege stammen aus der jeweils eingereichten oder lokal geöffneten Excel-Datei.

Die Datenbank enthält die Original-Vertrags-PDFs, ihre Regelversionen, die strukturierten Plandaten und die Prüfdetails. Die vier Prüfwerte lassen sich über die Tabelle `results` abrufen. Sparkasse ist über `run_contracts` dem Prüflauf zugeordnet. API-Zugangsdaten und lokale Ausgaben bleiben außerhalb des Repos.

## Outlook-Verbindung testen

Die App-Registrierung erfordert ein Microsoft-Entra-Verzeichnis, in dem du Anwendungen registrieren darfst. Wenn dein privates Outlook-Konto beim Portal im Mandanten „Microsoft Services“ landet und abgewiesen wird, beschreibt Microsoft die Einrichtung eines Azure-Kontos mit eigenem Verzeichnis. Das private Outlook-Postfach bleibt das Konto, mit dem Finn sich später anmeldet. [Microsoft-Hilfe zum Mandantenfehler](https://learn.microsoft.com/en-us/troubleshoot/entra/entra-id/app-integration/error-code-aadsts50020-user-account-identity-provider-does-not-exist)

1. In [Outlook im Web](https://outlook.live.com/mail/0/options/mail/forwarding) mit dem Finn-Postfach anmelden und IMAP aktivieren.
2. Im [Microsoft Entra Admin Center](https://entra.microsoft.com/) unter **App-Registrierungen** eine App namens Finn registrieren. Einen Kontotyp auswählen, der persönliche Microsoft-Konten einschließt. Unter **Authentifizierung** die Plattform **Mobile und Desktopanwendungen** mit Redirect-URI `http://localhost` hinzufügen. Die **Anwendungs-ID (Client-ID)** aus der Übersicht übernehmen. Ein Client-Secret ist für diesen Desktop-Anmeldeablauf nicht erforderlich.
3. In der lokalen `.env` die Postfachadresse als `FINN_MAIL_ADDRESS` und die Client-ID als `FINN_MAIL_CLIENT_ID` setzen. Den Test aus dem Projektordner starten.

```sh
python -m pip install -r requirements-mail.txt
python mail_check.py
```

Der Test öffnet die Microsoft-Anmeldung im Browser. Er öffnet den Posteingang per IMAP im Lesemodus und prüft die SMTP-Anmeldung. Er versendet keine Nachricht und speichert das Anmeldetoken nicht auf der Festplatte.

[Microsoft-Anleitung für Desktop-Anmeldung](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens) · [Microsoft-Berechtigungen für IMAP und SMTP](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth)

## Finn für neue Auftragsmails starten

Beim ersten Mail-Setup `mail-regeln.example.txt` als `mail-regeln.txt` in den Finn-Ordner kopieren. In der gut sichtbaren Textdatei die freigegebenen Adressen bei `ABSENDER=` kommagetrennt eintragen und die Rückfrageadresse bei `KONTAKT=` ergänzen. Die persönliche Datei wird von Git ignoriert. `.env` dient separat für Postfachadresse, Client-ID und KI-Zugang. Finn liest die Freigabeliste beim nächsten Abruf mit neuen Nachrichten erneut.

Start aus dem Projektordner mit dem bereits eingerichteten Outlook-Zugang.

```sh
.venv/bin/python mail_finn.py --graph
```

Nach der Microsoft-Anmeldung wartet Finn auf neue Nachrichten und fragt Outlook alle 15 Sekunden ab. Beim ersten Start werden bereits vorhandene Mails übersprungen. Für eine Probe von einer freigegebenen Adresse eine Mail mit dem Betreff **Wirtschaftsplan 2027** ohne Anhang senden, sobald **„Postfach eingerichtet“** angezeigt wurde. Der Lauf verschickt echte Antworten. Mit Strg+C beenden.

### Freigabeliste und Sicherheit

Das Finn-Postfach ist von außen erreichbar. Die lokale Freigabeliste begrenzt deshalb, welche Absender Aufträge auslösen können. Finn prüft die Absenderadresse anhand der Kopfzeilen. Für nicht freigegebene Adressen verschickt Finn nur einen Hinweis zur Freigabe und lädt weder Nachrichtentext noch Anhänge. Automatische Nachrichten und typische Verteiler überspringt er. Eine Vertragsbestätigung akzeptiert er nur von derselben weiterhin freigegebenen Adresse, die den Entwurf angefordert hat.

Die Freigabeliste ist eine Schutzmaßnahme des Prototyps. Sie prüft die Adresse im Absenderfeld. Ein produktiver Betrieb braucht zusätzlich eine serverseitige Prüfung der Absenderidentität und getrennte Rechte für Planprüfung und Vertragsfreigabe. Für das Testpostfach sind synthetische Daten vorgesehen.

### Versandstatus

Die lokale Datei `output/mail-status.sqlite3` speichert den IMAP-Fortschritt und fertige Antworten, die noch nicht zum Versand angenommen wurden. Bei temporären Ablehnungen wie Graph HTTP 503 oder SMTP 451 versucht Finn dieselbe Antwort bis zu dreimal erneut zu senden. Die Versuche bleiben auch nach einem Neustart vorgemerkt. Eine von Graph vorgegebene Wartezeit wird berücksichtigt.

Bei einer unklaren Übermittlung hält Finn die Antwort lokal zurück und meldet den Status im Terminal. Prüfe dann den Gesendet-Ordner und etwaige Zustellberichte. Nach ausgeschöpften Versuchen bleibt die Antwort ebenfalls lokal gespeichert.

## Wirtschaftsplan aus Excel prüfen

Der Mailprozess prüft akzeptierte Excel-Anhänge mit dem vorhandenen Rechenkern. Die strukturierte Ergebnis-Mail geht im ursprünglichen Verlauf zurück. Das Original der Excel-Datei, die HTML-Antwort und der Prüfbericht liegen im Ordner des Prüflaufs unter `output/`. DuckDB hält die ausgelesenen Werte, Zellen und Prüfergebnisse fest.

Der Import unterstützt Jahrespläne für 2026 und 2027 mit GuV, Bilanz und Finanzierung beziehungsweise den dokumentierten Alternativnamen. Er sucht die vorgesehenen Zeilenbezeichnungen und Jahresüberschriften, sodass weitere Zahlen und Vorjahresspalten nicht in die Berechnung eingehen. Er liest gespeicherte Excel-Werte. Formeln ohne gespeichertes Ergebnis müssen vorher in Excel berechnet und gespeichert werden. Andere Tabellenlayouts und mehrdeutige Zuordnungen erfordern eine angepasste Zuordnung. Bei einem Importfehler erhält der freigegebene Absender eine Rückmeldung.

Der Import unterstützt Euro, Tausend Euro und Millionen Euro. GuV-Positionen folgen den Vorzeichen der Vorlage. Darlehenszinsen und planmäßige Tilgung erwartet Finn als positive Beträge.

## Mailaufträge

Bei mehreren Begriffen im Betreff hat **Wirtschaftsplan** Vorrang. Danach folgen **Covenant-Übersicht** und **Vertrag prüfen**.

- **Wirtschaftsplan 2027** mit genau einer .xlsx-Datei. Finn sendet die kompakte Ergebnis-Mail mit **Finn-Pruefbericht.pdf** als Anhang. Der Anhang enthält Vertragsbelege, Excel-Fundstellen und Rechenwege.
- **Covenant-Übersicht**, **Covenant Übersicht** oder **Covenant Uebersicht** ohne Anhang. Finn nennt die vier geprüften Regeln, ihre Grenzwerte und Fundstellen.
- **Vertrag prüfen** oder **Vertrag pruefen** im Betreff mit genau einem PDF. Zusätze im Betreff sind möglich. Finn sendet den PDF-Text an die in der lokalen `.env` konfigurierte Chat-Completions-API. Er speichert den Prüfentwurf und antwortet mit den Regeln und Fundstellen aus dem eingereichten PDF. Ein Vergleich mit dem Vertragsbestand findet bei diesem Auftrag nicht statt. Dafür müssen HTTPS-Endpunkt, Modell und API-Key gesetzt sein.
- **Vertrag bestätigen <Kennung>** ohne Anhang. Nur der ursprüngliche, weiterhin freigegebene Absender kann den Entwurf per Mail bestätigen. Finn protokolliert die Bestätigung. Eine ausdrücklich vorgeschlagene und belegte Fremdkapitalquote wird per Bestätigung im gewählten Bestand aktiviert. Für weitere Formeln ist eine fachliche Zuordnung zum Rechenkern erforderlich.

Der Excel-Import gleicht die planmäßige Tilgung mit den Einzelpositionen ab und prüft die Eigenkapitalsumme sowie Aktiva gegen Passiva. Fehlt eine eindeutige Aussage über frei verfügbare Liquidität in der Vorlage, bleibt der Verschuldungsgrad offen. Die explizite Vorlagenzeile für ausstehende eingeforderte Einlagen wird berücksichtigt. Ein KI-Import für abweichende Excel-Layouts ist noch nicht angebunden.

Für die Aufnahme eines zusätzlichen Covenants liegt der synthetische Mustervertrag zur Fremdkapitalquote unter `beispiele/Mustervertrag-Fremdkapitalquote.pdf`. Finn schlägt beim Auftrag **Vertrag prüfen** die belegte Grenze und die Rechenregel vor. Die Bestätigung aktiviert die Regel im gewählten Bestand.

## Testbestand vollständig aus Vertragsmails aufbauen

Die lokale `.env` benötigt `FINN_API_URL`, `FINN_API_MODEL` und `FINN_API_KEY`. Für KI:connect ist der Endpunkt `https://chat.kiconnect.nrw/api/v1/chat/completions`. Modellnamen und Schlüssel in der lokalen Datei pflegen.

1. Finn im Projektordner mit `.venv/bin/python mail_finn.py --testbestand` starten. Der separate DuckDB-Bestand liegt unter `output/mail-test/`. Bei der ersten Verwendung ist er leer.
2. Von einer freigegebenen Adresse vier einzelne Mails mit dem Betreff **Vertrag prüfen** senden. Pro Mail eines der vier unveränderten Original-PDFs anhängen.
3. Die vier KI-Entwürfe mit den PDFs abgleichen und über **Vertrag bestätigen <Kennung>** von derselben Adresse bestätigen. Finn aktiviert ein bekanntes Original nur, wenn Dateihash, Anzahl, Grenzwerte und Vergleichsrichtungen zu den geprüften Rechenregeln passen. Abweichende PDFs werden als Entwürfe zur fachlichen Prüfung gespeichert.
4. **Covenant-Übersicht** senden und vier Verträge sowie vier aktive Covenants kontrollieren.
5. **Wirtschaftsplan 2027** mit der Original-Excel senden. Finn prüft im Testmodus die bestätigten Verträge und nennt ihren Umfang in der Antwort. Der KI-Abgleich prüft die Python-Zuordnung anhand von Zeilennamen, Jahresüberschriften und Einheiten. Er kann belegte Hinweise im Prüfbericht dokumentieren. Zellwerte und Kennzahlen verarbeitet Python. Ein ausgefallener KI-Abgleich wird im Prüfbericht kenntlich gemacht.

Ohne `--testbestand` verwendet Finn den geprüften Referenzbestand. Der IMAP-Abrufstand ist für beide Modi derselbe, damit ein Moduswechsel bereits bearbeitete Mails nicht erneut beantwortet.

### Versand über Microsoft Graph

Mit `--graph` liest Finn weiter per IMAP und meldet sich für den Versand separat mit **Mail.Send** an. Bei beiden Microsoft-Anmeldungen das gleiche Finn-Konto auswählen. Der Versandstatus HTTP 202 bedeutet, dass Graph die Nachricht angenommen hat. Die Zustellung lässt sich am Zielpostfach prüfen. Ohne `--graph` verwendet Finn SMTP. Beide Wege nutzen denselben Abrufstand und dieselbe lokale Versandwarteschlange.

## Robustheit und Regressionstests

Unterstützte Planjahre sind 2026 und 2027. Nennt der Mailbetreff ein Jahr, muss das Excel genau diesem Planjahr entsprechen. Ohne Jahr im Betreff wird ein eindeutiges gemeinsames Planjahr in GuV und Bilanz verwendet. Der Darlehensspiegel muss Zinsen, Tilgung und Endbestand für dasselbe Jahr ausweisen. Eine Hochrechnung zählt nicht als Plan. Finn prüft Planwerte gegen die geprüften Vertragsgrenzen und führt keinen Plan-Ist-Vergleich aus.

Finn findet Jahresüberschriften und Positionsbezeichnungen auch nach Verschiebungen. Einige gängige Alternativnamen und einzelne Tippfehler in längeren Wörtern werden erkannt. Bei mehreren plausiblen Treffern stoppt der Import. Die Darlehensbereiche müssen für den Summenabgleich weiterhin erkennbar gegliedert sein. Der KI-Abgleich dokumentiert fachliche Hinweise im Prüfbericht. Belegte KI-Zuordnungszweifel setzen die betroffenen Kennzahlen auf „nicht prüfbar“. Python prüft zusätzlich vorhandene GuV-Ergebnisüberleitungen. Gesonderte EBITDA-Bereinigungen und nicht eindeutig belegte Finanzschuldstrukturen führen zur fachlichen Klärung, bevor abhängige Kennzahlen bewertet werden.

```sh
.venv/bin/python -m pip install -r requirements-mail.txt
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
PYTHONPATH=. .venv/bin/python tests/stress_import.py beispiele/Wirtschaftsplan_2027.xlsx
```

Der Excel-Test erzeugt 30 synthetische Kopien und eine Ergebnistabelle unter `output/excel-robustheit/`. Er prüft unter anderem Jahresauswahl, Einheiten, verschobene Tabellen, Tippfehler, Grenzverletzungen, widersprüchliche Daten und mehrere simulierte Mailaufträge. Der Vertragstest liest alle vier Original-PDFs und simuliert die KI-Antworten. Die Versandtests simulieren Graph und SMTP, auch nach einem Neustart. Die Tests laufen ohne lokale Mail-Freigabeliste, Mailversand und API-Key.


Die Vertragstests enthalten zusätzlich synthetische Klauseln und absichtlich falsche KI-Vorschläge, etwa eine vertauschte Eigen- und Fremdkapitalquote bei identischer Zahl, falsche Einheiten, fehlende zweite Grenzen und unbekannte Kennzahlen. Finn verlangt einen Beleg für Kennzahl, Richtung, Grenzwert und Einheit. Für die im Mustervertrag belegte Fremdkapitalquote schlägt Finn die hinterlegte Formel samt Bilanzfeldern vor und aktiviert sie erst nach Bestätigung. Andere neue Kennzahlen bleiben Entwürfe, bis eine fachlich geprüfte Formel samt Excel-Zuordnung hinterlegt ist. Die Zahlen und Texte in `output/` sind lokale Testergebnisse und werden von Git ignoriert.
