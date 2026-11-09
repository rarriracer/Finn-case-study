import imaplib
import os
import smtplib
import ssl

from contracts import load_env


SCOPES = [
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "https://outlook.office.com/SMTP.Send",
]


class MailboxAuthenticationError(RuntimeError):
    pass


def open_inbox(address, token):
    auth = f"user={address}\x01auth=Bearer {token}\x01\x01"
    inbox = imaplib.IMAP4_SSL("outlook.office365.com", 993, ssl_context=ssl.create_default_context(), timeout=20)
    try:
        inbox.authenticate("XOAUTH2", lambda challenge: auth.encode("utf-8") if not challenge else b"")
        return inbox
    except imaplib.IMAP4.abort:
        inbox.shutdown()
        raise
    except imaplib.IMAP4.error as error:
        inbox.shutdown()
        raise MailboxAuthenticationError("Outlook hat die IMAP-Anmeldung abgelehnt.") from error
    except OSError:
        inbox.shutdown()
        raise


def open_sender(address, token):
    auth = f"user={address}\x01auth=Bearer {token}\x01\x01"
    sender = smtplib.SMTP("smtp-mail.outlook.com", 587, timeout=20)
    try:
        sender.ehlo()
        sender.starttls(context=ssl.create_default_context())
        sender.ehlo()
        sender.auth("XOAUTH2", lambda challenge=None: auth if challenge is None else "")
        return sender
    except Exception:
        sender.close()
        raise


def check_mail(address, token):
    with open_inbox(address, token) as inbox:
        status, _ = inbox.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("IMAP: Posteingang ließ sich nicht öffnen.")
        print("IMAP: Posteingang lesbar.")
    with open_sender(address, token):
        print("SMTP: Anmeldung erfolgreich. Keine Nachricht gesendet.")


def microsoft_app():
    load_env()
    address = os.getenv("FINN_MAIL_ADDRESS")
    client_id = os.getenv("FINN_MAIL_CLIENT_ID")
    if not address or not client_id:
        raise SystemExit("Bitte FINN_MAIL_ADDRESS und FINN_MAIL_CLIENT_ID in der lokalen .env setzen.")
    import msal

    return address, msal.PublicClientApplication(
        client_id, authority="https://login.microsoftonline.com/consumers",
    )


def interactive_token(app, address, scopes=SCOPES):
    result = app.acquire_token_interactive(scopes=scopes, login_hint=address)
    if "access_token" not in result:
        raise SystemExit(f"Microsoft-Anmeldung fehlgeschlagen ({result.get('error', 'unbekannter Fehler')}).")
    return result["access_token"]


def main():
    address, app = microsoft_app()
    try:
        check_mail(address, interactive_token(app, address))
    except (imaplib.IMAP4.error, smtplib.SMTPException, OSError, RuntimeError) as error:
        raise SystemExit(f"Mail-Verbindung fehlgeschlagen ({type(error).__name__}).") from None


if __name__ == "__main__":
    main()
