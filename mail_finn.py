import argparse
from contextlib import suppress
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, parsedate_to_datetime
from html import escape
from html.parser import HTMLParser
from http.client import HTTPException
import imaplib
from io import BytesIO
from math import ceil
from pathlib import Path
import re
import smtplib
import sqlite3
import time
from unicodedata import normalize
from urllib.error import HTTPError
from zipfile import BadZipFile, ZipFile

from mail_check import MailboxAuthenticationError, SCOPES, interactive_token, microsoft_app, open_inbox, open_sender
from mail_graph import GRAPH_SCOPES, send_graph
from presentation import BET_GRADIENT_ROW, BET_RED


ROOT = Path(__file__).resolve().parent
MIB = 1024 * 1024
INTERVAL = 15
MAX_RETRY_DELAY = 300
MAX_SEND_ATTEMPTS = 4
HEADERS = "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID REFERENCES AUTO-SUBMITTED PRECEDENCE LIST-ID RETURN-PATH CONTENT-TYPE)])"


def settings():
    pairs = {}
    for line in (ROOT / "mail-regeln.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            pairs[key.strip()] = value.strip()
    allowed = {address.strip().casefold() for address in pairs["ABSENDER"].split(",") if address.strip()}
    keyword = pairs["BETREFF"]
    limit = int(pairs["MAX_ANHANG_MB"])
    contact = pairs["KONTAKT"]
    if not allowed or not keyword or not contact or not 1 <= limit <= 25:
        raise ValueError("Bitte Absender, Betreff, Kontakt und eine Größenbegrenzung von 1 bis 25 eintragen.")
    return allowed, keyword, contact, limit


def source_address(message, own_address):
    header = message["From"]
    auto = str(message.get("Auto-Submitted", "")).split(";", 1)[0].strip().casefold()
    precedence = str(message.get("Precedence", "")).strip().casefold()
    if (
        auto not in ("", "no") or precedence in ("bulk", "list", "junk")
        or message.get("List-Id") or str(message.get("Return-Path", "")).strip() == "<>"
        or message.get_content_type() in ("multipart/report", "message/delivery-status")
        or len(message.get_all("From", [])) != 1 or header.defects or len(header.addresses) != 1
    ):
        return None
    address = header.addresses[0].addr_spec
    local_part = header.addresses[0].username.casefold()
    if re.search(r"(?:^|[._+-])(?:no[._-]?reply|do[._-]?not[._-]?reply|mailer[._-]?daemon|postmaster)(?:$|[._+-])", local_part):
        return None
    if not header.addresses[0].domain or address.casefold() == own_address.casefold():
        return None
    return address


def attached_files(message):
    return [part for part in message.walk() if part.get_content_disposition() == "attachment" or
            (part.get_filename() and not (part.get_content_maintype() == "image" and part.get_content_disposition() == "inline"))]


def excel_attachment(message, limit):
    files = attached_files(message)
    if not files:
        return None, ("Passende Datei anhängen", "Bitte hängen Sie den Wirtschaftsplan als Excel-Datei (.xlsx) an.")
    if len(files) > 1:
        return None, ("Passende Datei anhängen", "Bitte senden Sie pro Auftrag genau eine Excel-Datei (.xlsx).")
    part = files[0]
    content = part.get_payload(decode=True)
    if content is not None and len(content) > limit * MIB:
        return None, oversized(limit)
    if content is not None and (part.get_filename() or "").lower().endswith(".xlsx"):
        try:
            with ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
                if {"[Content_Types].xml", "xl/workbook.xml"} <= names:
                    return (content, part.get_filename()), None
        except BadZipFile:
            pass
    return None, ("Passende Datei anhängen", "Bitte senden Sie eine gültige Excel-Arbeitsmappe im Format .xlsx.")


def unapproved_result(contact):
    return "Ihre Adresse ist noch nicht freigegeben", f"Bitte wenden Sie sich für die Freigabe an {contact}."


def oversized(limit):
    return "Passende Datei anhängen", f"Bitte senden Sie die Excel-Datei mit höchstens {limit} MB als Anhang."


def make_reply(original, own_address, recipient, result, result_html=None, report=None, access_denied=False):
    title, text = result
    response = EmailMessage(policy=policy.SMTP)
    response["From"] = own_address
    response["To"] = recipient
    response["Date"] = formatdate()
    response["Message-ID"] = make_msgid(domain=own_address.rsplit("@", 1)[-1])
    subject = str(original.get("Subject", "")).strip()
    if access_denied:
        response["Subject"] = f"Finn: Freigabe erforderlich – {subject or 'Ihre Nachricht'}"
    else:
        response["Subject"] = subject if subject.lower().startswith("re:") else "Re: " + (subject or "Ihre Nachricht an Finn")
    response["Auto-Submitted"] = "auto-replied"
    response["X-Auto-Response-Suppress"] = "All"
    message_id = str(original.get("Message-ID", ""))
    if re.fullmatch(r"<[^<>\s]+>", message_id):
        response["In-Reply-To"] = message_id
        references = re.findall(r"<[^<>\s]+>", str(original.get("References", "")))
        response["References"] = " ".join([*references[-15:], message_id])
    response.set_content(f"Guten Tag,\n\n{title}\n{text}\n\nViele Grüße\nFinn\n")
    formatted_text = escape(text).replace("\n", "<br>")
    response.add_alternative(result_html or f"""<!doctype html><html lang="de"><body style="margin:0;background:#f8f7f4;color:#262220;font:16px/1.55 Arial,sans-serif">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 14px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;text-align:left">
<tr><td style="padding:0 0 14px;border-bottom:3px solid {BET_RED}">
<strong style="font-size:20px;color:{BET_RED}">Finn</strong> <span style="color:#665953;font-size:13px">Finanzierung</span>
</td></tr>{BET_GRADIENT_ROW}<tr><td style="padding-top:20px">
<h1 style="font:bold 30px/1.15 Georgia,serif;margin:0 0 18px">{escape(title)}</h1>
<p>Guten Tag,</p><p>{formatted_text}</p><p>Viele Grüße<br><strong>Finn</strong></p>
</td></tr></table></td></tr></table></body></html>""", subtype="html")
    if report is not None:
        response.add_attachment(report, maintype="application", subtype="pdf", filename="Finn-Pruefbericht.pdf")
    return response


def fetch(inbox, uid, query):
    status, data = inbox.uid("FETCH", str(uid), query)
    if status != "OK":
        raise RuntimeError(f"IMAP-Abruf fehlgeschlagen (UID {uid}).")
    return next((row for row in data if isinstance(row, tuple)), None)


def confirmation_id(text):
    match = re.fullmatch(r"vertrag\s+best(?:ä|ae)tigen\s+([0-9a-f]{32})", normalize("NFC", text).strip().casefold())
    return match[1] if match else None


class ReplyText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.stopped = False
        self.hidden = False

    def handle_starttag(self, tag, attrs):
        if tag == "blockquote" or any(key == "id" and value == "divRplyFwdMsg" for key, value in attrs):
            self.stopped = True
        if tag in ("head", "style", "script"):
            self.hidden = True
        if tag in ("br", "p", "div") and not self.hidden and not self.stopped:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("head", "style", "script"):
            self.hidden = False
        if tag in ("p", "div") and not self.hidden and not self.stopped:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden and not self.stopped:
            self.parts.append(data)


def body_confirmation(message):
    body = message.get_body(preferencelist=("plain", "html"))
    if body is None:
        return None
    text = body.get_content()
    if body.get_content_subtype() == "html":
        parser = ReplyText()
        parser.feed(text)
        text = "".join(parser.parts)
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    return confirmation_id(first_line)


def process(inbox, uid, own_address, rules, send, confirmed_only=False):
    output = ROOT / "output"
    if confirmed_only:
        output = output / "mail-test"
    allowed, keyword, contact, limit = rules
    header = fetch(inbox, uid, HEADERS)
    if header is None:
        return
    meta, raw = header
    message = BytesParser(policy=policy.default).parsebytes(raw)
    address = source_address(message, own_address)
    if not address:
        print(f"UID {uid} übersprungen · eigene, automatische oder ungültige Absendernachricht.", flush=True)
        return
    result_html = report = None
    subject = str(message.get("Subject", "")).strip()
    command = re.sub(r"^(?:(?:re|aw):\s*)+", "", normalize("NFC", subject), flags=re.I).strip().casefold()
    confirmation = confirmation_id(command)
    is_workbook = keyword.casefold() in command
    unapproved = address.casefold() not in allowed
    if unapproved:
        result = unapproved_result(contact)
    elif not is_workbook and re.search(r"\bcovenant(?:\s+|\s*[-–—]\s*)(?:ü|ue)bersicht\b", command):
        from contract_mail import inventory

        result = inventory(output, confirmed_only)
    else:
        size = re.search(rb"RFC822\.SIZE (\d+)", meta, re.I)
        # MIME base64 and line wrapping add approximately 37% to an attachment.
        within_limit = bool(size and int(size[1]) <= limit * MIB * 1.4 + MIB)
        full_message = None
        if not confirmation and within_limit:
            full = fetch(inbox, uid, "(BODY.PEEK[])")
            if full is None:
                return
            full_message = BytesParser(policy=policy.default).parsebytes(full[1])
            if not is_workbook:
                confirmation = body_confirmation(full_message)
        if is_workbook:
            result, result_html, report = review_workbook_mail(full_message, subject, keyword, limit, output, confirmed_only)
        elif confirmation:
            from contract_mail import confirm

            result = confirm(output, confirmation, address, confirmed_only)
        elif re.search(r"\bvertrag[\s-]+pr(?:ü|ue)fen\b", command):
            result = review_pdf(full_message, address, limit, output)
        else:
            result, result_html, report = review_workbook_mail(full_message, subject, keyword, limit, output, confirmed_only)
    send(make_reply(message, own_address, address, result, result_html, report, access_denied=unapproved), address)


def review_workbook_mail(message, subject, keyword, limit, output, confirmed_only):
    result_html = report = None
    subject_matches = keyword.casefold() in subject.casefold()
    if message is None:
        result = oversized(limit)
    else:
        workbook, result = excel_attachment(message, limit)
    if not subject_matches:
        subject_hint = f"Bitte nehmen Sie „{keyword}“ in den Betreff auf."
        if result is None:
            result = "Betreff anpassen", subject_hint
        else:
            result = "Betreff anpassen und passende Datei anhängen", f"{subject_hint} {result[1]}"
    elif result is None:
        years = {int(year) for year in re.findall(r"\b20\d{2}\b", subject)}
        if len(years) > 1:
            result = "Planjahr angeben", "Bitte nennen Sie genau ein Planjahr im Betreff."
        else:
            result, result_html, report = check_attachment(*workbook, output, confirmed_only, next(iter(years), None))
    return result, result_html, report


def review_pdf(message, address, limit, output):
    from contract_mail import review

    invalid = ("PDF-Vertrag anhängen", f"Bitte hängen Sie genau einen PDF-Vertrag mit höchstens {limit} MB an.")
    if message is None:
        return invalid
    files = attached_files(message)
    if len(files) != 1:
        return invalid
    filename = files[0].get_filename() or ""
    content = files[0].get_payload(decode=True)
    if not filename.lower().endswith(".pdf") or not content or len(content) > limit * MIB or not content.startswith(b"%PDF-"):
        return invalid
    return review(output, content, filename, address)


def check_attachment(content, filename, output, confirmed_only, requested_year=None):
    from finn import check_plan, scope_note
    from plan_excel import PlanImportError, import_plan
    from presentation import order, values

    try:
        plan = import_plan(content, filename, requested_year)
    except PlanImportError as error:
        return ("Wirtschaftsplan konnte nicht gelesen werden", str(error)), None, None

    from plan_review import audit_plan

    audit_plan(plan)
    try:
        run_id, results, result_html, _ = check_plan(plan, output, content, confirmed_only)
        report = (output / run_id / "pruefbericht.pdf").read_bytes()
    except ValueError as error:
        return ("Prüfung noch nicht möglich", str(error)), None, None
    checked = sorted((item for item in results if item["rule"]), key=order)
    coverage = scope_note(results, confirmed_only)
    rows = [coverage, ""] if coverage else []
    for item in checked:
        actual, limit, _ = values(item)
        rows.append(f'{item["bank"]} · {item["rule"]["name"]}: {actual}, {limit}. {item["status"]}.')
    return ("Covenant-Prüfung Ihres Wirtschaftsplans", "\n".join([*rows, "", "Der Prüfbericht ist angehängt."])), result_html, report


def mailbox_number(inbox, name):
    _, values = inbox.response(name)
    if not values or not values[0]:
        raise RuntimeError(f"Outlook meldet keinen Wert für {name}.")
    return int(values[0])


def poll(address, token, db, confirmed_only=False, graph_token=None):
    mailbox = address.casefold()
    send_pending(db, address, token, graph_token)
    with open_inbox(address, token) as inbox:
        if inbox.select("INBOX", readonly=True)[0] != "OK":
            raise RuntimeError("Posteingang nicht lesbar.")
        validity = mailbox_number(inbox, "UIDVALIDITY")
        next_uid = mailbox_number(inbox, "UIDNEXT")
        state = db.execute("SELECT validity, last_uid FROM inbox WHERE address = ?", (mailbox,)).fetchone()
        if state is None or state[0] != validity:
            with db:
                db.execute(
                    "INSERT INTO inbox VALUES (?, ?, ?) ON CONFLICT(address) DO UPDATE "
                    "SET validity = excluded.validity, last_uid = excluded.last_uid "
                    "WHERE inbox.validity != excluded.validity", (mailbox, validity, next_uid - 1),
                )
            print("Postfach eingerichtet. Finn berücksichtigt neue Mails ab jetzt.", flush=True)
            return
        last_uid = state[1]
        if next_uid <= last_uid + 1:
            return
        status, data = inbox.uid("SEARCH", None, "UID", f"{last_uid + 1}:{next_uid - 1}")
        if status != "OK":
            raise RuntimeError("Outlook-Suche fehlgeschlagen.")
        rules = settings()
        for uid in sorted(int(value) for value in data[0].split()):
            if uid <= last_uid:
                continue

            def queue(reply, recipient):
                payload = reply.as_bytes()
                with db:
                    if advance(db, mailbox, validity, uid):
                        db.execute(
                            "INSERT INTO outbox (address, validity, uid, recipient, payload, requires_approval) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (mailbox, validity, uid, recipient, payload, recipient.casefold() in rules[0]),
                        )

            process(inbox, uid, address, rules, queue, confirmed_only)
            with db:
                advance(db, mailbox, validity, uid)
            send_pending(db, address, token, graph_token)


def init_mail_state(db):
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS inbox (address TEXT PRIMARY KEY, validity INTEGER, last_uid INTEGER)")
        db.execute("""
            CREATE TABLE IF NOT EXISTS outbox (
                address TEXT NOT NULL, validity INTEGER NOT NULL, uid INTEGER NOT NULL,
                recipient TEXT NOT NULL, payload BLOB NOT NULL, requires_approval INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, last_error TEXT,
                PRIMARY KEY (address, validity, uid)
            )
        """)


def advance(db, mailbox, validity, uid):
    return db.execute(
        "UPDATE inbox SET last_uid = ? WHERE address = ? AND validity = ? AND last_uid < ?",
        (uid, mailbox, validity, uid),
    ).rowcount == 1


def send_pending(db, address, token, graph_token):
    mailbox = address.casefold()
    ready = db.execute(
        "SELECT validity, uid, recipient, payload, requires_approval, attempts FROM outbox "
        "WHERE address = ? AND state = 'pending' AND next_attempt <= ? ORDER BY validity, uid",
        (mailbox, time.time()),
    ).fetchall()
    if not ready:
        return
    allowed = settings()[0]
    for validity, uid, recipient, payload, requires_approval, attempts in ready:
        key = (mailbox, validity, uid)
        if requires_approval and recipient.casefold() not in allowed:
            with db:
                db.execute(
                    "UPDATE outbox SET state = 'failed', last_error = 'Freigabe entzogen' "
                    "WHERE address = ? AND validity = ? AND uid = ? AND state = 'pending'", key,
                )
            print(f"UID {uid}: Versand gestoppt. Die Empfängeradresse ist nicht mehr freigegeben.", flush=True)
            continue
        # A crash during submission leaves an uncertain outcome instead of triggering a second send.
        with db:
            claimed = db.execute(
                "UPDATE outbox SET state = 'uncertain', attempts = attempts + 1, last_error = 'Versand unterbrochen' "
                "WHERE address = ? AND validity = ? AND uid = ? AND state = 'pending' AND attempts = ?",
                (*key, attempts),
            ).rowcount
        if claimed:
            submit_reply(db, key, address, recipient, payload, token, graph_token, attempts + 1)


def submit_reply(db, key, address, recipient, payload, token, graph_token, attempts):
    uid = key[2]
    started = False
    try:
        if graph_token is not None:
            started = True
            send_graph(payload, graph_token)
        else:
            reply = BytesParser(policy=policy.SMTP).parsebytes(payload)
            smtp = open_sender(address, token)
            try:
                started = True
                smtp.send_message(reply, from_addr=address, to_addrs=[recipient])
            finally:
                with suppress(OSError):
                    smtp.close()
    except (OSError, HTTPException, RuntimeError) as error:
        state, reason = send_failure(error, recipient, started)
        if attempts >= MAX_SEND_ATTEMPTS and state == "pending":
            state = "failed"
        delay = retry_delay(error, attempts) if state == "pending" else 0
        with db:
            db.execute(
                "UPDATE outbox SET state = ?, next_attempt = ?, last_error = ? "
                "WHERE address = ? AND validity = ? AND uid = ?",
                (state, time.time() + delay, reason, *key),
            )
        if state == "pending":
            print(f"UID {uid}: {reason}. Neuer Versandversuch frühestens in {delay} Sekunden.", flush=True)
            if isinstance(error, smtplib.SMTPAuthenticationError) or isinstance(error, HTTPError) and error.code == 401:
                raise
        elif state == "uncertain":
            print(f"UID {uid}: Versandstatus unklar ({reason}). Bitte Gesendet-Ordner und Zustellberichte prüfen.", flush=True)
        else:
            print(f"UID {uid}: Versand fehlgeschlagen ({reason}). Die Antwort bleibt lokal gespeichert.", flush=True)
        return
    with db:
        db.execute("DELETE FROM outbox WHERE address = ? AND validity = ? AND uid = ?", key)
    transport = "Graph" if graph_token is not None else "SMTP"
    print(f"UID {uid}: Antwort von {transport} zum Versand angenommen.", flush=True)


def send_failure(error, recipient, started):
    if isinstance(error, HTTPError):
        if error.code in (401, 429, 503):
            return "pending", f"Graph HTTP {error.code}"
        return ("failed" if 400 <= error.code < 500 else "uncertain"), f"Graph HTTP {error.code}"
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        code = error.recipients.get(recipient, (None,))[0]
    elif isinstance(error, smtplib.SMTPResponseException):
        code = error.smtp_code
    else:
        code = None
    if isinstance(error, smtplib.SMTPAuthenticationError) or (code is not None and 400 <= code < 500):
        return "pending", f"SMTP {code}"
    if code is not None and 500 <= code < 600:
        return "failed", f"SMTP {code}"
    return ("uncertain" if started else "pending"), type(error).__name__


def retry_delay(error, attempts):
    delay = min(MAX_RETRY_DELAY, INTERVAL * 2 ** (attempts - 1))
    if isinstance(error, HTTPError) and error.headers:
        retry_after = error.headers.get("Retry-After")
        if retry_after:
            with suppress(ValueError, TypeError, OverflowError):
                seconds = int(retry_after) if retry_after.isascii() and retry_after.isdecimal() else (
                    parsedate_to_datetime(retry_after).timestamp() - time.time()
                )
                return max(delay, ceil(seconds))
    return delay


def outlook_token(app, account, force_refresh, scopes=SCOPES):
    result = app.acquire_token_silent_with_error(scopes, account=account, force_refresh=force_refresh)
    if result and result.get("access_token"):
        return result["access_token"]
    code = (result or {}).get("error")
    if code in ("temporarily_unavailable", "server_error"):
        raise RuntimeError("Microsofts Anmeldedienst ist vorübergehend nicht erreichbar.")
    raise SystemExit("Microsoft-Anmeldung muss erneuert werden. Bitte Finn neu starten und erneut anmelden.")


def retry_reason(error):
    if isinstance(error, (MailboxAuthenticationError, smtplib.SMTPAuthenticationError)):
        return "Outlook hat die Mail-Anmeldung abgelehnt"
    if isinstance(error, imaplib.IMAP4.abort):
        return "IMAP-Verbindung wurde unterbrochen"
    if isinstance(error, imaplib.IMAP4.error):
        return "Outlook hat den IMAP-Abruf abgelehnt"
    if isinstance(error, smtplib.SMTPException):
        return "SMTP-Versand fehlgeschlagen"
    if isinstance(error, HTTPError):
        return f"Microsoft Graph meldet HTTP {error.code}"
    if isinstance(error, OSError):
        return f"Netzwerkfehler ({type(error).__name__})"
    return f"Verarbeitung fehlgeschlagen ({type(error).__name__})"


def watch(address, app, token, db, once, confirmed_only=False, graph_token=None):
    accounts = app.get_accounts()
    account = next((item for item in accounts if item.get("username", "").casefold() == address.casefold()), None)
    if account is None and len(accounts) == 1:
        account = accounts[0]
    imap_scopes = SCOPES[:1] if graph_token is not None else SCOPES
    delay = INTERVAL
    refresh = False
    force_refresh = False
    while True:
        try:
            if refresh:
                if account is None:
                    raise SystemExit("Microsoft-Anmeldung konnte keinem Konto zugeordnet werden. Bitte Finn neu starten.")
                token = outlook_token(app, account, force_refresh, imap_scopes)
                if graph_token is not None:
                    graph_token = outlook_token(app, account, force_refresh, GRAPH_SCOPES)
            refresh = True
            poll(address, token, db, confirmed_only, graph_token)
        except (OSError, imaplib.IMAP4.error, smtplib.SMTPException, RuntimeError, ValueError, KeyError) as error:
            auth_error = (
                isinstance(error, (MailboxAuthenticationError, smtplib.SMTPAuthenticationError))
                or (isinstance(error, HTTPError) and error.code == 401)
            )
            if once:
                raise SystemExit(retry_reason(error)) from None
            if auth_error and force_refresh:
                raise SystemExit("Outlook lehnt die Anmeldung auch nach Token-Erneuerung ab. Bitte Finn neu starten und erneut anmelden.") from None
            if auth_error:
                force_refresh = True
            print(f"{retry_reason(error)}. Nächster Versuch in {delay} Sekunden.", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, MAX_RETRY_DELAY)
            continue
        if once:
            return
        if delay > INTERVAL:
            print("Outlook-Abruf wieder erfolgreich.", flush=True)
        force_refresh = False
        delay = INTERVAL
        time.sleep(INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="Finn ruft Outlook ab und beantwortet neue Nachrichten.")
    parser.add_argument("--once", action="store_true", help="Einmal abrufen")
    parser.add_argument("--testbestand", action="store_true", help="Separaten Bestand aus bestätigten Vertragsmails verwenden")
    parser.add_argument("--graph", action="store_true", help="Antworten über Microsoft Graph statt SMTP versenden")
    args = parser.parse_args()
    try:
        settings()
    except FileNotFoundError:
        parser.exit(1, "Bitte mail-regeln.example.txt als mail-regeln.txt kopieren und Absender sowie Kontakt eintragen.\n")
    except (KeyError, ValueError):
        parser.exit(1, "Bitte in mail-regeln.txt Absender, Betreff, Kontakt und eine Anhanggrenze von 1 bis 25 MB eintragen.\n")
    address, app = microsoft_app()
    token = interactive_token(app, address, scopes=SCOPES[:1] if args.graph else SCOPES)
    graph_token = None
    if args.graph:
        print("Microsoft-Anmeldung für Graph Mail.Send wird geöffnet.", flush=True)
        graph_token = interactive_token(app, address, scopes=GRAPH_SCOPES)
        if len(app.get_accounts()) != 1:
            raise SystemExit("Bitte für IMAP und Microsoft Graph dasselbe Finn-Konto auswählen.")
    transport = "Graph" if args.graph else "SMTP"
    print(f"Microsoft-Anmeldung erfolgreich. Abruf alle 15 Sekunden, Versand über {transport}.", flush=True)
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    with sqlite3.connect(output / "mail-status.sqlite3") as db:
        init_mail_state(db)
        for uid, reason in db.execute(
            "SELECT uid, last_error FROM outbox WHERE address = ? AND state IN ('uncertain', 'failed') "
            "ORDER BY validity, uid", (address.casefold(),),
        ):
            print(f"UID {uid}: Antwort lokal gespeichert, Versand offen ({reason}).", flush=True)
        try:
            watch(address, app, token, db, args.once, args.testbestand, graph_token)
        except KeyboardInterrupt:
            print("\nFinn beendet.")


if __name__ == "__main__":
    main()
