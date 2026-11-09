"""Exercise the mail loop with local state and simulated transports."""

from contextlib import closing, redirect_stdout
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from io import StringIO
from pathlib import Path
import smtplib
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from mail_finn import INTERVAL, MAX_SEND_ATTEMPTS, init_mail_state, poll, send_pending


MAILBOX = "finn@example.org"
SENDER = "finance@example.org"
RULES = ({SENDER}, "Wirtschaftsplan", "kontakt@example.org", 10)
ROOT = Path(__file__).resolve().parents[1]


def graph_error(code, headers=None):
    return HTTPError("https://graph.microsoft.com/v1.0/me/sendMail", code, "", headers, None)


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        work = Path(self.enter(TemporaryDirectory()))
        self.db_path = work / "mail.sqlite3"
        self.db = self.enter(closing(sqlite3.connect(self.db_path)))
        # Simulate an existing installation that only has the IMAP checkpoint.
        self.db.execute("CREATE TABLE inbox (address TEXT PRIMARY KEY, validity INTEGER, last_uid INTEGER)")
        with self.db:
            self.db.execute("INSERT INTO inbox VALUES (?, 1, 10)", (MAILBOX,))
        init_mail_state(self.db)
        self.clock = self.enter(patch("mail_finn.time.time", return_value=1_000))
        self.rules = self.enter(patch("mail_finn.settings", return_value=RULES))
        self.enter(patch("mail_finn.ROOT", work))
        self.enter(patch("plan_review.api_ready", return_value=False))
        self.log = self.enter(redirect_stdout(StringIO()))
        self.inbox = MagicMock()
        self.inbox.__enter__.return_value = self.inbox
        self.inbox.select.return_value = ("OK", [b"1"])
        self.inbox.response.side_effect = lambda key: (key, [b"1" if key == "UIDVALIDITY" else b"12"])
        self.inbox.uid.return_value = ("OK", [b"11"])
        self.enter(patch("mail_finn.open_inbox", return_value=self.inbox))
        self.graph = self.enter(patch("mail_finn.send_graph"))
        self.smtp = self.enter(patch("mail_finn.open_sender"))
        self.message = EmailMessage()
        self.message["From"] = SENDER
        self.message["Subject"] = "Wirtschaftsplan 2027"
        self.message["Message-ID"] = "<auftrag@example.org>"
        self.message.set_content("Bitte prüfen.")
        self.fetch = self.enter(patch("mail_finn.fetch", side_effect=self.fetch_message))

    def enter(self, manager):
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def fetch_message(self, *_):
        return b"RFC822.SIZE 50000", self.message.as_bytes()

    def run_poll(self, graph=True, db=None):
        poll(MAILBOX, "mail-token", db or self.db, graph_token="graph-token" if graph else None)

    def queued(self):
        return self.db.execute("SELECT state, attempts, next_attempt, last_error FROM outbox").fetchall()

    def test_graph_503_survives_restart_and_reuses_full_reply(self):
        path = ROOT / "beispiele" / "Wirtschaftsplan_2027.xlsx"
        self.message.add_attachment(path.read_bytes(), maintype="application", subtype="octet-stream", filename=path.name)
        self.graph.side_effect = [graph_error(503, {"Retry-After": "45"}), None]
        self.run_poll()
        self.assertEqual(self.queued(), [("pending", 1, 1_045, "Graph HTTP 503")])
        original_bytes = self.graph.call_args.args[0]
        reply = BytesParser(policy=policy.default).parsebytes(original_bytes)
        self.assertEqual(reply["In-Reply-To"], self.message["Message-ID"])
        report, = reply.iter_attachments()
        self.assertEqual(report.get_content_type(), "application/pdf")

        with closing(sqlite3.connect(self.db_path)) as restarted:
            init_mail_state(restarted)
            self.clock.return_value = 1_044
            self.run_poll(db=restarted)
            self.assertEqual(self.graph.call_count, 1)
            self.clock.return_value = 1_045
            self.run_poll(db=restarted)
            self.run_poll(db=restarted)
        self.assertEqual(self.graph.call_count, 2)
        self.assertEqual(self.graph.call_args.args[0], original_bytes)
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.db.execute("SELECT last_uid FROM inbox").fetchone(), (11,))

    def test_graph_stops_after_bounded_retries(self):
        self.graph.side_effect = graph_error(503)
        for attempt in range(MAX_SEND_ATTEMPTS + 2):
            self.clock.return_value = 1_000 + 400 * attempt
            self.run_poll()
        self.assertEqual(self.graph.call_count, MAX_SEND_ATTEMPTS)
        self.assertEqual(self.queued()[0][:2], ("failed", MAX_SEND_ATTEMPTS))

    def test_temporary_failure_does_not_block_following_mail(self):
        self.inbox.response.side_effect = lambda key: (key, [b"1" if key == "UIDVALIDITY" else b"13"])
        self.inbox.uid.return_value = ("OK", [b"11 12"])
        self.graph.side_effect = [graph_error(503), None]
        self.run_poll()
        self.assertEqual(self.graph.call_count, 2)
        self.assertEqual(self.db.execute("SELECT uid, state FROM outbox").fetchall(), [(11, "pending")])
        self.assertEqual(self.db.execute("SELECT last_uid FROM inbox").fetchone(), (12,))

    def test_graph_429_waits_and_permanent_rejection_stays_failed(self):
        self.graph.side_effect = [graph_error(429, {"Retry-After": "60"}), graph_error(403)]
        self.run_poll()
        self.assertEqual(self.queued(), [("pending", 1, 1_060, "Graph HTTP 429")])
        self.clock.return_value = 1_060
        self.run_poll()
        self.run_poll()
        self.assertEqual(self.graph.call_count, 2)
        self.assertEqual(self.queued(), [("failed", 2, 1_060, "Graph HTTP 403")])

    def test_lost_response_stays_uncertain(self):
        self.graph.side_effect = TimeoutError()
        self.run_poll()
        self.clock.return_value = 1_500
        self.run_poll()
        self.assertEqual(self.graph.call_count, 1)
        self.assertEqual(self.queued()[0][:2], ("uncertain", 1))

    def test_interrupted_send_is_not_replayed_by_another_connection(self):
        def interrupt(*_):
            with closing(sqlite3.connect(self.db_path)) as other:
                send_pending(other, MAILBOX, "mail-token", "graph-token")
            raise KeyboardInterrupt

        self.graph.side_effect = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_poll()
        self.graph.side_effect = None
        self.run_poll()
        self.assertEqual(self.graph.call_count, 1)
        self.assertEqual(self.queued()[0][:2], ("uncertain", 1))

    def test_smtp_retries_explicit_temporary_rejection(self):
        smtp = self.smtp.return_value
        smtp.send_message.side_effect = [smtplib.SMTPDataError(451, b"Try later"), {}]
        self.run_poll(graph=False)
        self.assertEqual(self.queued(), [("pending", 1, 1_000 + INTERVAL, "SMTP 451")])
        self.clock.return_value += INTERVAL
        self.run_poll(graph=False)
        self.assertEqual(smtp.send_message.call_count, 2)
        first, second = smtp.send_message.call_args_list
        self.assertEqual(first.args[0]["Message-ID"], second.args[0]["Message-ID"])
        self.assertEqual(self.queued(), [])

    def test_pending_reply_respects_removed_approval(self):
        self.graph.side_effect = [graph_error(503), None]
        self.run_poll()
        self.rules.return_value = ({"other@example.org"}, *RULES[1:])
        self.clock.return_value += INTERVAL
        self.run_poll()
        self.assertEqual(self.graph.call_count, 1)
        self.assertEqual(self.queued()[0][3], "Freigabe entzogen")
