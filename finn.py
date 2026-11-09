import argparse
from datetime import datetime, timezone
from decimal import InvalidOperation
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import duckdb

from calculation import FIELDS, calculate, normalize
from contracts import api_ready, check_source, load_env, pdf_pages, review_candidates
from presentation import render


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
DEMO_WORKBOOK = ROOT / "beispiele" / "Wirtschaftsplan_2027.xlsx"


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def reference_contracts():
    return json.loads((FIXTURES / "seed-rules.json").read_text(encoding="utf-8"))


def store_contracts(db, contracts=None):
    if contracts is None:
        contracts = reference_contracts()
    for contract in contracts:
        path = FIXTURES / contract["file"]
        pdf = path.read_bytes()
        store_contract(db, contract, pdf)
    return contracts


def store_contract(db, contract, pdf):
    version = digest(pdf)
    contract["version"] = version
    pages = pdf_pages(pdf)
    db.execute("INSERT OR IGNORE INTO contracts VALUES (?, ?, ?, ?, ?)",
               [version, contract["id"], contract["bank"], contract["file"], pdf])
    for rule in contract["rules"]:
        for page, _, quote in rule["sources"]:
            check_source(pages, page, quote)
        rule_version = digest(dump([version, rule]).encode())
        db.execute("INSERT OR IGNORE INTO rules VALUES (?, ?, ?, ?)",
                   [rule_version, version, rule["id"], dump(rule)])
        rule["version"] = rule_version


def contracts_for_run(db, confirmed_only):
    if not confirmed_only and not db.execute(
        "SELECT 1 FROM initialization WHERE key = 'reference_contracts'"
    ).fetchone():
        for contract in store_contracts(db):
            db.execute(
                "INSERT OR IGNORE INTO active_contracts VALUES (?, ?)",
                [contract["id"], contract["version"]],
            )
        db.execute("INSERT INTO initialization VALUES ('reference_contracts')")
    return active_contracts(db)


def store_plan(db, plan):
    source_data = dump(plan)
    version = digest(source_data.encode())
    db.execute("INSERT OR IGNORE INTO plans VALUES (?, ?, ?, ?)",
               [version, plan["name"], plan["date"], source_data])
    for field, entry in plan["inputs"].items():
        try:
            converted = str(normalize(entry))
        except (KeyError, ValueError, TypeError, InvalidOperation):
            converted = None
        raw, unit, source = entry
        db.execute("INSERT OR IGNORE INTO plan_inputs VALUES (?, ?, ?, ?, ?, ?)",
                   [version, field, dump(raw), unit, source, converted])
    return version


def active_contracts(db):
    contracts = []
    for version, bank, filename in db.execute(
        "SELECT c.version, c.bank, c.filename FROM active_contracts a "
        "JOIN contracts c ON a.contract_version = c.version ORDER BY a.contract_id"
    ).fetchall():
        rules = []
        for rule_version, definition in db.execute(
            "SELECT version, definition FROM rules WHERE contract_version = ? ORDER BY rule_id", [version],
        ).fetchall():
            rule = json.loads(definition)
            rule["version"] = rule_version
            rules.append(rule)
        contracts.append({"version": version, "bank": bank, "file": filename, "rules": rules})
    return contracts


def run_check(db, plan, confirmed_only=False):
    run_id = str(uuid4())
    contracts = contracts_for_run(db, confirmed_only)
    if not contracts:
        scope = "Testbestand" if confirmed_only else "Vertragsbestand"
        raise ValueError(f"Im {scope} ist kein Vertrag aktiv.")
    plan_version = store_plan(db, plan)
    db.execute("INSERT INTO runs VALUES (?, ?, ?)", [run_id, plan_version, datetime.now(timezone.utc)])
    results = []
    for contract in contracts:
        db.execute("INSERT INTO run_contracts VALUES (?, ?)", [run_id, contract["version"]])
        for rule in contract["rules"] or [None]:
            result = {
                "bank": contract["bank"], "filename": contract["file"], "rule": rule,
                "status": "Kein Financial Covenant", "value": None, "headroom": None,
            }
            results.append(result)
            if rule is None:
                continue
            inputs = plan["inputs"]
            result["inputs"] = {key: inputs[key] for key in FIELDS[rule["id"]] if key in inputs}
            try:
                if rule.get("first_as_of", "") > plan["date"]:
                    raise ValueError("Der Planstichtag liegt vor dem ersten vertraglichen Prüfungsstichtag.")
                issues = plan.get("input_issues", {})
                relevant = [issues[key] for key in FIELDS[rule["id"]] if key in issues]
                if relevant:
                    raise ValueError(" ".join(relevant))
                value, gap, numerator, denominator = calculate(rule, inputs)
                result.update(value=str(value), headroom=str(gap),
                              numerator=str(numerator), denominator=str(denominator),
                              status="Eingehalten" if gap >= 0 else "Verletzt")
            except (KeyError, ValueError, TypeError, InvalidOperation) as error:
                result.update(status="Nicht prüfbar", error=str(error))
            db.execute("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?)", [
                run_id, rule["version"], result["status"], result["value"], result["headroom"], dump(result),
            ])
    return run_id, results


def main():
    parser = argparse.ArgumentParser(description="Finn · lokale Covenant-Prüfung")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Den Original-Wirtschaftsplan als Excel prüfen")
    demo.add_argument("--plan", type=Path, default=DEMO_WORKBOOK, help="Pfad zur Excel-Datei")
    demo.add_argument("--year", type=int, help="Planjahr bei mehreren Planspalten")
    excel = commands.add_parser("excel", help="Wirtschaftsplan direkt aus der Excel-Vorlage prüfen")
    excel.add_argument("workbook", type=Path)
    excel.add_argument("--year", type=int, help="Planjahr bei mehreren Planspalten")
    ingest = commands.add_parser("extract", help="KI-Vorschläge mit PDF-Belegen zur Prüfung speichern")
    ingest.add_argument("pdfs", nargs="*", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.command == "extract":
        load_env()
        if not api_ready():
            parser.exit(1, "Bitte FINN_API_URL, FINN_API_MODEL und FINN_API_KEY in der lokalen .env setzen.\n")
        for pdf in args.pdfs or sorted(FIXTURES.glob("Vertrag*.pdf")):
            destination = args.output / f"{pdf.stem}.review.json"
            destination.write_text(dump(review_candidates(pdf)), encoding="utf-8")
            print(destination)
        return
    from plan_excel import PlanImportError, import_plan

    path = args.workbook if args.command == "excel" else args.plan
    try:
        workbook = path.read_bytes()
        plan = import_plan(workbook, path.name, args.year)
    except (OSError, PlanImportError) as error:
        parser.exit(1, f"Excel-Datei konnte nicht eingelesen werden. {error}\n")
    run_id, _, _, _ = check_plan(plan, args.output, workbook)
    for name in ("antwort.html", "pruefbericht.pdf"):
        print(args.output / run_id / name)


def scope_note(results, confirmed_only):
    if not confirmed_only:
        return ""
    count = len({item["filename"] for item in results})
    noun = "Vertrag" if count == 1 else "Verträge"
    return f"Prüfungsumfang: {count} {noun} aus dem Testbestand."



def check_plan(plan, output, workbook=None, confirmed_only=False):
    from report_pdf import make_report_pdf

    output.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(output / "finn.duckdb")) as db:
        db.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        db.begin()
        run_id, results = run_check(db, plan, confirmed_only)
        coverage = scope_note(results, confirmed_only)
        mail, report = render(plan, results, run_id, coverage)
        pdf_report = make_report_pdf(plan, results, run_id, coverage)
        db.commit()
    if workbook is not None:
        destination = output / run_id / "wirtschaftsplan.xlsx"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(workbook)
    for name, content in (("antwort.html", mail), ("pruefbericht.html", report)):
        destination = output / run_id / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    (output / run_id / "pruefbericht.pdf").write_bytes(pdf_report)
    return run_id, results, mail, report


if __name__ == "__main__":
    main()
