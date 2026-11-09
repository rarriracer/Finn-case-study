"""Read-only inventory and review drafts for contract mail commands."""

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import PureWindowsPath
from urllib.error import HTTPError
from uuid import uuid4

import duckdb
from pypdf.errors import PyPdfError

from contracts import api_ready, metric_identity, review_candidates
from finn import FIXTURES, ROOT, contracts_for_run, digest, dump, reference_contracts, store_contract, store_contracts
from proposed_rules import debt_ratio_proposal
from presentation import fmt


def connect(output):
    output.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(output / "finn.duckdb"))


def inventory(output, confirmed_only=False):
    with connect(output) as db:
        db.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        db.begin()
        contracts = contracts_for_run(db, confirmed_only)
        db.commit()
    lines = []
    for contract in contracts:
        if not contract["rules"]:
            lines.append(f'{contract["bank"]} · keine Kennzahlengrenze')
        for rule in contract["rules"]:
            page, section, _ = rule["sources"][0]
            operator = "mindestens" if rule["operator"] == ">=" else "höchstens"
            decimals = rule["decimals"] if rule["decimals"] is not None else 1
            lines.append(
                f'{contract["bank"]} · {rule["name"]}, {operator} '
                f'{fmt(rule["threshold"], decimals)} {rule["unit"]} ({section}, Seite {page})'
            )
    return "Covenant-Übersicht", "\n".join(lines) or "Dieser Bestand enthält noch keine bestätigten Verträge."


def review(output, content, filename, sender):
    filename = PureWindowsPath(filename).name
    document_label = f"Datei: {filename}"
    if not api_ready():
        return "Vertragsprüfung noch nicht verfügbar", f"{document_label}\n\nDie KI-Schnittstelle für Vertragsprüfungen ist noch nicht eingerichtet."
    pdf_hash = digest(content)
    try:
        candidates = review_candidates(content)
        executable = debt_ratio_proposal(content, candidates)
    except (PyPdfError, OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        reason = f"KI-API HTTP {error.code}" if isinstance(error, HTTPError) else type(error).__name__
        if isinstance(error, ValueError):
            if str(error).startswith(("Textbeleg fehlt", "Ungültige PDF-Seite")):
                reason = "KI-Fundstelle stimmt nicht mit dem PDF überein"
            elif not isinstance(error, json.JSONDecodeError):
                reason = str(error)
        print(f"Vertragsprüfung fehlgeschlagen · {filename!r} · {reason}", flush=True)
        return (
            "Vertragsprüfung nicht abgeschlossen",
            f"{document_label}\n\nDie Prüfung konnte nicht abgeschlossen werden. Grund: {reason}.",
        )
    if executable:
        candidates["executable_proposal"] = executable
    identifier = uuid4().hex
    with connect(output) as db:
        db.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        db.execute(
            "INSERT INTO contract_reviews VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            [identifier, sender.casefold(), filename, pdf_hash, content,
             dump(candidates), datetime.now(timezone.utc)],
        )
    lines = [document_label, "Prüfentwurf. Die Grenzwerte wurden am PDF belegt. Bitte prüfen Sie die KI-Beschreibung der Definition, Rundung und Gültigkeit fachlich."]
    for rule in candidates["rules"]:
        refs = ", ".join(f'Seite {source["page"]}, {source["section"]}' for source in rule["sources"])
        lines.append(f'{rule["name"]} · {rule["operator"]} {rule["threshold"]} {rule["unit"]} ({refs})\n{rule["definition"]}\nPrüfzeitraum: {rule.get("period") or "offen"}. Rundung: {rule.get("rounding") or "keine Angabe"}.')
    if not candidates["rules"]:
        lines.append("Im PDF wurde keine Kennzahlengrenze gefunden. Bitte prüfen Sie diese Einschätzung.")
    reference = matched_reference(pdf_hash, candidates)
    if executable:
        lines.append(
            f'Rechenregel zur Freigabe: {executable["formula"]}. '
            f'Grenze: höchstens {executable["threshold"].replace(".", ",")} %. '
            f'Kaufmännische Rundung auf eine Nachkommastelle. Erster Stichtag: {executable["first_as_of"]}. '
            "Benötigte Planwerte: Summe der Verbindlichkeiten, Summe der Rückstellungen und Bilanzsumme. "
            "Mit Ihrer Bestätigung wird diese Regel für künftige Planprüfungen im gewählten Bestand aktiviert."
        )
    elif reference is None:
        lines.append("Für diesen Entwurf ist keine passende geprüfte Rechenregel hinterlegt. Finn berechnet daraus noch keine Kennzahl.")
    lines.append(
        f"Wenn der Entwurf fachlich geprüft ist, antworten Sie mit „Vertrag bestätigen {identifier}“ als erster Zeile im Mailtext oder im Betreff. "
        "Bei der Bestätigung prüft Finn, ob eine freigegebene Rechenregel für das Dokument hinterlegt ist."
    )
    return "Vertragsprüfung zur Bestätigung", "\n\n".join(lines)



def original_reference(pdf_hash):
    return next((
        contract for contract in reference_contracts()
        if digest((FIXTURES / contract["file"]).read_bytes()) == pdf_hash
    ), None)


def matched_reference(pdf_hash, proposal):
    contract = original_reference(pdf_hash)
    if contract is not None:
        try:
            expected = Counter((r["id"], r["operator"], Decimal(r["threshold"]), r["unit"]) for r in contract["rules"])
            aliases = {"x": "×", "×": "×", "fach": "×", "%": "%", "prozent": "%"}
            observed = Counter(
                (metric_identity(r["name"]), r["operator"], Decimal(str(r["threshold"]).replace(",", ".")),
                 aliases.get(str(r["unit"]).strip().casefold()))
                for r in proposal["rules"]
            )
        except (KeyError, InvalidOperation, TypeError):
            return None
        return contract if observed == expected else None
    return None


def confirm(output, identifier, sender, confirmed_only=False):
    with connect(output) as db:
        db.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        db.begin()
        row = db.execute(
            "SELECT filename, confirmed_at IS NOT NULL, pdf_hash, proposal, pdf FROM contract_reviews WHERE id = ? AND requester = ?",
            [identifier, sender.casefold()],
        ).fetchone()
        if row is None:
            return "Prüfentwurf nicht gefunden", "Bitte verwenden Sie die Kennung aus Ihrer Prüfmail und senden Sie von derselben Adresse."
        candidates = json.loads(row[3])
        reference = matched_reference(row[2], candidates) if confirmed_only else None
        proposal = candidates.get("executable_proposal")
        executable = debt_ratio_proposal(row[4], candidates) if proposal else None
        activated_name = None
        if executable and executable == proposal and digest(row[4]) == row[2]:
            contract = {"id": f"mail-{row[2]}", "bank": f"Vertrag {row[0]}", "file": row[0], "rules": [executable]}
            store_contract(db, contract, row[4])
            db.execute("INSERT OR REPLACE INTO active_contracts VALUES (?, ?)", [contract["id"], contract["version"]])
            activated_name = "Die Fremdkapitalquote"
        if reference:
            contract = store_contracts(db, [reference])[0]
            db.execute(
                "INSERT OR REPLACE INTO active_contracts VALUES (?, ?)",
                [contract["id"], contract["version"]],
            )
            activated_name = reference["bank"]
        if not row[1]:
            db.execute(
                "UPDATE contract_reviews SET confirmed_at = ? WHERE id = ? AND confirmed_at IS NULL",
                [datetime.now(timezone.utc), identifier],
            )
        db.commit()
    if activated_name:
        scope = "Testbestand" if confirmed_only else "Vertragsbestand"
        return "Vertrag bestätigt und aktiviert", f"Datei: {row[0]}\n\n{activated_name} ist im {scope} aktiv und wird in künftigen Prüfungen berücksichtigt."
    return "Bestätigung gespeichert", f"Ihre Bestätigung für {row[0]} ist erfasst. Der aktive Regelsatz wird separat aktualisiert."
