"""Send one Finn reply through Microsoft Graph for delivery diagnostics."""

import argparse
from email.message import EmailMessage
from urllib.error import HTTPError, URLError

from mail_check import interactive_token, microsoft_app
from mail_graph import GRAPH_SCOPES, send_graph
from mail_finn import make_reply, settings, unapproved_result




def main():
    parser = argparse.ArgumentParser(description="Eine Finn-Testantwort über Microsoft Graph senden.")
    parser.add_argument("recipient")
    parser.add_argument("--subject", default="Test")
    parser.add_argument("--in-reply-to")
    args = parser.parse_args()
    allowed, _, contact, _ = settings()
    if args.recipient.casefold() in allowed:
        parser.error("Für diesen Test eine Adresse außerhalb der Freigabeliste wählen.")

    address, app = microsoft_app()
    original = EmailMessage()
    original["Subject"] = args.subject
    if args.in_reply_to:
        original["Message-ID"] = args.in_reply_to
    reply = make_reply(original, address, args.recipient, unapproved_result(contact))
    print("Microsoft-Anmeldung für Graph Mail.Send wird geöffnet.", flush=True)
    token = interactive_token(app, address, scopes=GRAPH_SCOPES)
    try:
        send_graph(reply, token)
    except HTTPError as error:
        raise SystemExit(f"Microsoft Graph hat den Versand abgelehnt (HTTP {error.code}).") from None
    except URLError as error:
        raise SystemExit(f"Microsoft Graph konnte nicht erreicht werden ({error.reason}).") from None
    print("Graph hat die Testantwort angenommen (HTTP 202). Bitte den Zielposteingang und das Finn-Postfach auf einen Zustellbericht prüfen.")


if __name__ == "__main__":
    main()
