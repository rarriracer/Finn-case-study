"""Read annual plan workbooks without evaluating formulas."""

from decimal import Decimal
from difflib import SequenceMatcher
from hashlib import sha256
from io import BytesIO
from math import isfinite
from pathlib import PureWindowsPath
import re
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException


SUPPORTED_YEARS = {2026, 2027}
MAX_UNPACKED = 50 * 1024 * 1024
LABELS = {
    "GuV": {
        "net_income": "Jahresüberschuss",
        "income_tax": "Steuern vom Einkommen und vom Ertrag",
        "interest_expense": "Zinsen und ähnliche Aufwendungen",
        "interest_income": "Sonstige Zinsen und ähnliche Erträge",
        "depreciation": "Abschreibungen auf immaterielle Vermögensgegenstände und Sachanlagen",
        "provision_interest": "davon aus der Aufzinsung von Pensionsrückstellungen (nachrichtlich, nicht zahlungswirksam)",
    },
    "Bilanz": {
        "equity": "A. Eigenkapital",
        "assets": "Summe Aktiva (Bilanzsumme)",
        "special_items": "B. Sonderposten für Investitions- und Baukostenzuschüsse",
        "bank_debt": "Verbindlichkeiten gegenüber Kreditinstituten",
        "cash": "Kassenbestand und Guthaben bei Kreditinstituten",
    },
}
FINANCING = {
    "loan_interest": ("Summe Darlehen gesamt", "Zinsaufwand {year}"),
    "scheduled_principal": ("Summe Darlehen gesamt", "Planmäßige Tilgung {year}"),
    "bank_debt_schedule": ("Summe Kreditinstitute", "Stand 31.12.{year}"),
}
ALIASES = {
    "GuV": ("Erfolgsplan", "Gewinn- und Verlustrechnung"),
    "Bilanz": ("Planbilanz", "Vermögensplan"),
    "Finanzierung": ("Darlehensspiegel", "Finanzierungsplan"),
    "Jahresüberschuss": ("Jahresergebnis",),
    "A. Eigenkapital": ("Eigenkapital gesamt", "Summe Eigenkapital"),
    "Summe Aktiva (Bilanzsumme)": ("Bilanzsumme", "Summe Aktiva"),
    "D. Verbindlichkeiten": ("Summe Verbindlichkeiten", "Verbindlichkeiten gesamt"),
    "C. Rückstellungen": ("Summe Rückstellungen", "Rückstellungen gesamt"),
}
YEAR_TOKEN = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
PLAN_TOKEN = re.compile(r"\bplan(?:werte?|jahr|ung)?\b")
NON_PLAN = re.compile(r"\b(?:ist|hr|hochrechnung|forecast|vorjahr)\b")


class PlanImportError(ValueError):
    pass


def label(value):
    return " ".join(str(value or "").split()).casefold()


def typo_match(value, target):
    left, right = value.split(), target.split()
    if len(left) != len(right):
        return False
    pairs = [(a, b) for a, b in zip(left, right) if a != b]
    if len(pairs) != 1:
        return False
    a, b = pairs[0]
    if min(len(a), len(b)) < 7 or not (a.isalpha() and b.isalpha()):
        return False
    edits = [edit for edit in SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if edit[0] != "equal"]
    if len(edits) == 1:
        _, i, j, k, end = edits[0]
        return max(j - i, end - k) == 1
    return len(a) == len(b) and any(a[:i] + a[i:i + 2][::-1] + a[i + 2:] == b for i in range(len(a) - 1))


def positions(values, target, aliases=(), typos=False):
    names = [label(name) for name in (target, *aliases)]
    matches = []
    for index, value in enumerate(values, 1):
        text = label(value)
        if text in names or (typos and any(typo_match(text, name) for name in names)):
            matches.append(index)
    return matches


def unique_position(values, target, location):
    matches = positions(values, target, ALIASES.get(target, ()), typos=True)
    if len(matches) != 1:
        raise PlanImportError(f"„{target}“ ist in {location} nicht eindeutig zu finden.")
    return matches[0]


def plan_headers(rows):
    return [
        (row, col, int(years[0]))
        for row, cells in enumerate(rows, 1)
        for col, value in enumerate(cells, 1)
        if isinstance(value, str)
        and PLAN_TOKEN.search(label(value))
        and not NON_PLAN.search(label(value))
        and len(years := YEAR_TOKEN.findall(value)) == 1
        # A plan header has only a period, a plan qualifier, and an optional scenario name.
        and len(value.split()) <= 5
        and not re.search(r"wirtschaftsplan|finanzierungsplan|szenario|mio|t€", label(value))
    ]


def header_for(rows, year, sheet):
    matches = [(r, c) for r, c, y in plan_headers(rows) if y == year and c > 1]
    if len(matches) != 1:
        raise PlanImportError(f"Die Planspalte für {year} ist in „{sheet}“ nicht eindeutig.")
    return matches[0]


def labels_in(rows, header_row, header_col, default=1):
    matches = positions(rows[header_row - 1][:header_col - 1], "Position", ("Bezeichnung", "Posten"))
    if len(matches) > 1:
        raise PlanImportError("Mehrere Spalten für Positionsbezeichnungen gefunden.")
    col = matches[0] if matches else default
    return [row[col - 1] for row in rows]


def select_year(guv, balance, requested_year):
    if requested_year is not None:
        if requested_year not in SUPPORTED_YEARS:
            raise PlanImportError("Für die geprüften Vertragsregeln werden Plan 2026 und Plan 2027 unterstützt.")
        return requested_year
    years = {y for _, col, y in plan_headers(guv) if col > 1} & {y for _, col, y in plan_headers(balance) if col > 1}
    if len(years) != 1:
        raise PlanImportError("Das Planjahr ist nicht eindeutig. Bitte nennen Sie das gewünschte Jahr im Betreff.")
    return select_year(guv, balance, years.pop())


def read_rows(workbook, name):
    index = unique_position(workbook.sheetnames, name, "den Tabellenblättern")
    sheet = workbook[workbook.sheetnames[index - 1]]
    if (sheet.max_row or 0) > 500 or (sheet.max_column or 0) > 50:
        raise PlanImportError(f"Das Tabellenblatt „{name}“ ist größer als die unterstützte Vorlage.")
    return list(sheet.iter_rows(max_row=500 if sheet.max_row is None else sheet.max_row,
                                max_col=50 if sheet.max_column is None else sheet.max_column,
                                values_only=True))


def unit_for(sheet_rows, title, default):
    heading = " ".join(label(cell) for row in sheet_rows for cell in row if isinstance(cell, str))
    if re.search(r"\b(?:usd|chf|gbp|dollar|franken)\b|[$£]", heading):
        raise PlanImportError(f"Die Währung in „{title}“ wird nicht unterstützt.")
    units = re.findall(r"(?<!\w)(mio\.?\s*(?:€|eur)|t\s*(?:€|eur)|tausend\s+euro|€|eur|euro)(?!\w)", heading)
    normalized = {
        "Mio. €" if unit.startswith("mio") else "T€" if unit.startswith("t") else "€"
        for unit in units
    }
    if len(normalized) > 1:
        raise PlanImportError(f"Die Einheit in „{title}“ ist nicht eindeutig.")
    return next(iter(normalized), default)


def amount(rows, row, column, sheet, unit):
    source = f"{sheet}!{get_column_letter(column)}{row}"
    value = rows[row - 1][column - 1]
    if type(value) not in (int, float) or not isfinite(value):
        raise PlanImportError(
            f"In „{sheet}“ fehlt ein gespeicherter Zahlenwert ({source}). "
            "Bitte berechnen und speichern Sie die Arbeitsmappe in Excel erneut."
        )
    return [value, unit, source]


def read_workbook(workbook, requested_year):
    sheets = {name: read_rows(workbook, name) for name in (*LABELS, "Finanzierung")}
    cover = next((name for name in workbook.sheetnames if label(name) == "deckblatt"), None)
    default = None
    if cover:
        declarations = [
            tuple(cell for cell in row if isinstance(cell, str) and re.search(r"alle beträge|alle werte", label(cell)))
            for row in read_rows(workbook, cover)[:15]
        ]
        default = unit_for(declarations, "Deckblatt", None)
    year = select_year(sheets["GuV"], sheets["Bilanz"], requested_year)
    inputs, issues, evidence = {}, {}, {}
    for sheet, fields in LABELS.items():
        rows = sheets[sheet]
        header, column = header_for(rows, year, sheet)
        row_labels = labels_in(rows, header, column)
        unit = unit_for(rows[:header], sheet, default)
        if unit is None:
            raise PlanImportError(f"Die Einheit in „{sheet}“ fehlt.")
        actual_sheet = workbook.sheetnames[unique_position(workbook.sheetnames, sheet, "den Tabellenblättern") - 1]
        for field, row_label in fields.items():
            row = unique_position(row_labels, row_label, sheet)
            inputs[field] = amount(rows, row, column, actual_sheet, unit)
            evidence[field] = {
                "label": row_labels[row - 1], "heading": rows[header - 1][column - 1], "unit_heading": unit,
            }
        if sheet == "GuV":
            validate_guv(rows, row_labels, column, unit, actual_sheet, inputs, issues)
        if sheet == "Bilanz":
            validate_balance(rows, row_labels, column, unit, actual_sheet, inputs, issues)
            for field, title in (("liabilities", "D. Verbindlichkeiten"), ("provisions", "C. Rückstellungen")):
                try:
                    row = unique_position(row_labels, title, sheet)
                    inputs[field] = amount(rows, row, column, actual_sheet, unit)
                except PlanImportError as error:
                    issues[field] = str(error)
                else:
                    evidence[field] = {
                        "label": row_labels[row - 1], "heading": rows[header - 1][column - 1], "unit_heading": unit,
                    }

    sheet = "Finanzierung"
    rows = sheets[sheet]
    titles = {field: title.format(year=year) for field, (_, title) in FINANCING.items()}
    finance_aliases = {
        "scheduled_principal": (f"Regeltilgung {year}",),
        "loan_interest": (f"Darlehenszinsen {year}",),
        "bank_debt_schedule": (f"Endbestand {year}",),
    }
    headings = [(index, row) for index, row in enumerate(rows, 1)
                if all(positions(row, title, finance_aliases[field], typos=True)
                       for field, title in titles.items())]
    if len(headings) != 1:
        raise PlanImportError(f"Die Spalten für Zinsen, Tilgung und Endbestand {year} fehlen oder sind im Darlehensspiegel nicht eindeutig.")
    header, cells = headings[0]
    columns = {}
    for field, title in titles.items():
        matches = positions(cells, title, finance_aliases[field], typos=True)
        if len(matches) != 1:
            raise PlanImportError(f"„{title}“ ist im Darlehensspiegel nicht eindeutig.")
        columns[field] = matches[0]
    label_cols = positions(cells, "Darlehen", ("Position", "Bezeichnung"))
    if len(label_cols) > 1:
        raise PlanImportError("Mehrere Darlehensbezeichnungen im Darlehensspiegel gefunden.")
    label_col = label_cols[0] if label_cols else 1
    row_labels = [row[label_col - 1] for row in rows]
    unit = unit_for(rows[:header], sheet, default)
    if unit is None:
        raise PlanImportError("Die Einheit im Darlehensspiegel fehlt.")
    actual_sheet = workbook.sheetnames[unique_position(workbook.sheetnames, sheet, "den Tabellenblättern") - 1]
    for field, (row_label, _) in FINANCING.items():
        row = unique_position(row_labels, row_label, sheet)
        column = columns[field]
        inputs[field] = amount(rows, row, column, actual_sheet, unit)
        evidence[field] = {
            "label": row_labels[row - 1], "heading": cells[column - 1], "unit_heading": unit,
        }
    validate_repayments(rows, row_labels, columns["scheduled_principal"], inputs, issues)
    validate_financing_scope(sheets, rows, row_labels, columns["bank_debt_schedule"], issues)
    inputs["extraordinary_adjustment"] = [0, "T€", "Vorlagenannahme: keine außerordentlichen Bereinigungen"]
    return year, inputs, issues, evidence


def same_total(total, components):
    return abs(Decimal(str(total)) - sum(Decimal(str(item)) for item in components)) <= Decimal("0.000001")


def validate_guv(rows, labels, column, unit, sheet, inputs, issues):
    # Compare only explicitly presented totals; never infer missing amounts.
    for total, components in (
        ("Jahresüberschuss", ("Ergebnis vor Ertragsteuern", "Steuern vom Einkommen und vom Ertrag")),
        ("Ergebnis vor Ertragsteuern", ("Betriebsergebnis", "Finanzergebnis")),
        ("Finanzergebnis", ("Sonstige Zinsen und ähnliche Erträge", "Zinsen und ähnliche Aufwendungen")),
    ):
        matches = [positions(labels, name, ALIASES.get(name, ()), typos=True) for name in (total, *components)]
        if not all(matches):
            continue
        try:
            if any(len(found) != 1 for found in matches):
                raise PlanImportError("Mehrdeutige GuV-Zwischensumme")
            amounts = [amount(rows, found[0], column, sheet, unit)[0] for found in matches]
            if not same_total(amounts[0], amounts[1:]):
                raise PlanImportError(f"Die GuV-Überleitung für „{total}“ stimmt nicht mit den Einzelwerten überein.")
        except PlanImportError as error:
            issues["net_income"] = str(error)
    text = " ".join(label(cell) for row in rows for cell in row if isinstance(cell, str))
    if re.search(r"au(?:ß|ss)erordentlich|ebitda.?bereinigung", text):
        issues["extraordinary_adjustment"] = "Gesonderte EBITDA-Bereinigungen sind ausgewiesen. Bitte deren Betrag und vertragliche Anwendbarkeit fachlich klären."


def validate_financing_scope(sheets, rows, labels, column, issues):
    subtotal = unique_position(labels, "Summe Kreditinstitute", "Finanzierung")
    end = unique_position(labels, "Summe Darlehen gesamt", "Finanzierung")
    other_loans = [label(labels[i]) for i in range(subtotal, end - 1)
                   if labels[i] and label(labels[i]) != "sonstige darlehen"
                   and rows[i][column - 1] != 0]
    balance_text = " ".join(label(cell) for row in sheets["Bilanz"] for cell in row if isinstance(cell, str))
    qualified = lambda text: ("gesellschafter" in text and "qualifiziert" in text and "rangrücktritt" in text
                              and not re.search(r"nicht|ohne|kein", text))
    notes = [label(cell) for row in sheets["Bilanz"] for cell in row
             if isinstance(cell, str) and "gesellschafter" in label(cell)]
    if (any(not qualified(text) for text in other_loans)
        or (other_loans and (not notes or any(not qualified(text) for text in notes)))
        or re.search(r"finanzierungsleasing|schuldschein|anleihe|factoring", balance_text)):
        issues["bank_debt"] = "Die Finanzschulden sind nicht eindeutig auf Bankschulden und nachweislich nachrangige Gesellschafterdarlehen begrenzt. Zusammensetzung und Rangrücktritt bitte fachlich prüfen."


def validate_balance(rows, row_labels, column, unit, sheet, inputs, issues):
    required = ("Gezeichnetes Kapital", "Kapitalrücklage", "Gewinnrücklagen", "Bilanzgewinn")
    values = [
        amount(rows, unique_position(row_labels, name, "Bilanz"), column, sheet, unit)[0]
        for name in required
    ]
    liabilities_row = unique_position(row_labels, "Summe Passiva", "Bilanz")
    liabilities = amount(rows, liabilities_row, column, sheet, unit)[0]
    if not same_total(inputs["equity"][0], values):
        issues["equity"] = "Das Eigenkapital stimmt nicht mit den ausgewiesenen Einzelposten überein."
    if not same_total(inputs["assets"][0], [liabilities]):
        issues["assets"] = "Aktiva und Passiva stimmen nicht überein."

    notes = [label(value) for value in row_labels if "liquide mittel:" in label(value)]
    expected = "liquide mittel: frei verfügbar, keine verpfändung, keine cash-pooling-verbindlichkeiten."
    if len(notes) != 1 or not re.fullmatch(r"(?:3\) )?" + re.escape(expected), notes[0]):
        issues["cash"] = "Die frei verfügbare Liquidität ist in dieser Vorlage nicht eindeutig ausgewiesen."

    capital_label = "Eingeforderte, aber noch nicht eingezahlte Einlagen"
    capital_rows = positions(row_labels, capital_label, typos=True)
    if len(capital_rows) == 1:
        inputs["unpaid_called_capital"] = amount(rows, capital_rows[0], column, sheet, unit)
    elif capital_rows:
        issues["unpaid_called_capital"] = "Ausstehende eingeforderte Einlagen sind mehrfach ausgewiesen."
    else:
        inputs["unpaid_called_capital"] = [0, "T€", "Vorlagenannahme: keine ausstehenden eingeforderten Einlagen"]


def validate_repayments(rows, labels, column, inputs, issues):
    first = unique_position(labels, "Verbindlichkeiten gegenüber Kreditinstituten", "Finanzierung")
    subtotal = unique_position(labels, "Summe Kreditinstitute", "Finanzierung")
    end = unique_position(labels, "Summe Darlehen gesamt", "Finanzierung")
    if not first < subtotal < end:
        issues["scheduled_principal"] = "Die Reihenfolge der Tilgungspositionen ist nicht eindeutig."
        return
    subtotal_value = rows[subtotal - 1][column - 1]
    bank_parts = [rows[i][column - 1] for i in range(first, subtotal - 1) if labels[i]]
    other_parts = [rows[i][column - 1] for i in range(subtotal, end - 1)
                   if labels[i] and label(labels[i]) != "sonstige darlehen"]
    parts = bank_parts + other_parts
    if (type(subtotal_value) not in (int, float)
        or any(type(value) not in (int, float) or not isfinite(value) for value in parts)
        or not same_total(subtotal_value, bank_parts)
        or not same_total(inputs["scheduled_principal"][0], parts)
    ):
        issues["scheduled_principal"] = "Die planmäßige Tilgung stimmt nicht mit den Einzelpositionen im Darlehensspiegel überein."


def import_plan(content, filename, requested_year=None):
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNPACKED:
                raise PlanImportError("Die entpackte Excel-Datei überschreitet 50 MB.")
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
        try:
            year, inputs, issues, evidence = read_workbook(workbook, requested_year)
        finally:
            workbook.close()
    except PlanImportError:
        raise
    except (BadZipFile, InvalidFileException, ParseError, DefusedXmlException,
            IndexError, KeyError, TypeError, ValueError, OSError, OverflowError, RuntimeError) as error:
        raise PlanImportError("Die Datei lässt sich nicht als unterstützte Excel-Vorlage lesen.") from error
    return {
        "name": PureWindowsPath(filename).name,
        "date": f"{year}-12-31",
        "workbook_sha256": sha256(content).hexdigest(),
        "inputs": inputs,
        "input_issues": issues,
        "mapping_evidence": evidence,
    }
