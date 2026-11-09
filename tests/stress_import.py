from decimal import Decimal
from pathlib import Path
import argparse
import re
from tempfile import TemporaryDirectory
from unittest.mock import patch
from email.message import EmailMessage
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl import load_workbook
from finn import check_plan
from mail_finn import process
from plan_excel import PlanImportError, import_plan

parser = argparse.ArgumentParser(description="30 synthetische Varianten der originalen Case-Study-Datei prüfen")
parser.add_argument("source", type=Path)
parser.add_argument("--output", type=Path, default=Path("output/excel-robustheit"))
args = parser.parse_args()
source = args.source
output = args.output
output.mkdir(parents=True, exist_ok=True)
ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
ET.register_namespace("", ns[1:-1])
with ZipFile(source) as workbook:
    parts = {name: workbook.read(name) for name in workbook.namelist()}

def variant(name, edits=(), rename=None):
    modified = dict(parts)
    for number, changes in edits:
        key = f"xl/worksheets/sheet{number}.xml"
        tree = ET.fromstring(modified[key])
        data = tree.find(ns+"sheetData")
        cells = {c.get("r"): c for row in data for c in row}
        for coordinate, value in changes.items():
            if coordinate not in cells:
                row_number = re.search(r"\d+", coordinate).group()
                row = next((r for r in data if r.get("r") == row_number), None)
                if row is None:
                    row = ET.SubElement(data, ns+"row", {"r": row_number})
                cells[coordinate] = ET.SubElement(row, ns+"c", {"r": coordinate})
            cell = cells[coordinate]
            if value == "UNCACHED":
                cell.remove(cell.find(ns+"v"))
                continue
            for child in list(cell):
                cell.remove(child)
            if isinstance(value, str):
                cell.set("t", "inlineStr")
                ET.SubElement(ET.SubElement(cell, ns+"is"), ns+"t").text = value
            elif value is None:
                cell.attrib.pop("t", None)
            else:
                cell.set("t", "n")
                ET.SubElement(cell, ns+"v").text = str(value)
        for row in data:
            row[:] = sorted(row, key=lambda cell: (len(re.match("[A-Z]+", cell.get("r"))[0]), cell.get("r")))
        data[:] = sorted(data, key=lambda row: int(row.get("r")))
        dimension = tree.find(ns+"dimension")
        if dimension is not None:
            dimension.set("ref", "A1:P55")
        modified[key] = ET.tostring(tree, encoding="utf-8")
    if rename:
        key = "xl/workbook.xml"
        tree = ET.fromstring(modified[key])
        next(sheet for sheet in tree.find(ns+"sheets") if sheet.get("name") == rename[0]).set("name",rename[1])
        modified[key] = ET.tostring(tree, encoding="utf-8")
    dest = output / f"{name}.xlsx"
    with ZipFile(dest, "w", ZIP_DEFLATED) as archive:
        for key, content in modified.items():
            archive.writestr(key, content)
    return dest


BASE = {"economic_equity": "34.32", "equity": "30.7", "dscr": "1.56", "leverage": "3.37"}
COVER = "Alle Beträge in T€ (Tausend Euro), soweit nicht anders angegeben."
GU_V_YEAR = { "D3": "Plan 2026" }
BALANCE_YEAR = { "D3": "31.12.2026 (Plan)" }
FIN_YEAR = {
    "A1": "Finanzierungsplan 2026 – in T€", "D3": "Planmäßige Tilgung 2026",
    "F3": "Stand 31.12.2026", "G3": "Zinsaufwand 2026",
}
YEAR_2026 = [(2, GU_V_YEAR), (3, BALANCE_YEAR), (4, FIN_YEAR)]
workbook = load_workbook(source, data_only=True, read_only=True)
try:
    cache = {
        index: {cell.coordinate: cell.value for row in sheet for cell in row if cell.value is not None}
        for index, sheet in enumerate(workbook, 1)
    }
finally:
    workbook.close()


def relocate(sheet, row_offset, col_offset):
    from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter

    edits = dict.fromkeys(cache[sheet])
    for coordinate, value in cache[sheet].items():
        row, col = coordinate_to_tuple(coordinate)
        edits[f"{get_column_letter(col + col_offset)}{row + row_offset}"] = value
    return edits


def units(sheet, heading, factor):
    return {
        "A1": heading,
        **{cell: round(value * factor, 8) for cell, value in cache[sheet].items() if type(value) in (int, float)},
    }


cases = [
    ("01-original", "Originalplan", [], None, None, 2027, BASE),
    ("02-plan-2026", "Eigenständiger Plan 2026, Regeltilgung erhöht", [*YEAR_2026[:-1], (4, {**FIN_YEAR, "D5": 8250, "D10": 24750, "D15": 25750})], None, 2026, 2026, {**BASE, "dscr": "1.21"}),
    ("03-plan-2026-ohne-betreffsjahr", "2026 aus Tabellenüberschriften", YEAR_2026, None, None, 2026, BASE),
    ("04-hr-bleibt-hr", "Auftrag 2026, Datei enthält dort nur Hochrechnung", [], None, 2026, None, None),
    ("05-plan-2028", "Plan außerhalb freigegebener Jahre", [(2, {"D3":"Plan 2028"}), (3, {"D3":"31.12.2028 (Plan)"})], None, 2028, None, None),
    ("06-mehrere-planjahre", "Zwei Planjahre ohne explizite Auswahl", [(2, {"E3":"Plan 2026"}), (3, {"E3":"31.12.2026 (Plan)"})], None, None, None, None),
    ("07-mehrere-planjahre-auswahl", "Zwei Planjahre, Auftrag 2027", [(2, {"E3":"Plan 2026"}), (3, {"E3":"31.12.2026 (Plan)"})], None, 2027, 2027, BASE),
    ("08-jahre-gemischt", "GuV/Bilanz 2026, Darlehensspiegel 2027", YEAR_2026[:2], None, 2026, None, None),
    ("09-doppelte-planspalte", "Zwei Spalten mit Plan 2027", [(2, {"E3":"Plan 2027","E18":1})], None, 2027, None, None),
    ("10-plan-spalte-verschoben", "GuV-Planspalte nach F, Szenario in D", [(2, {"D3":"Szenario", **{"F"+cell[1:]: value for cell,value in cache[2].items() if cell.startswith("D")}})], None, None, 2027, BASE),
    ("11-alle-tabellen-verschoben", "Tabellen zwei Zeilen tiefer und eine Spalte weiter rechts", [(i,relocate(i,2,1)) for i in (2,3,4)], None, 2027, 2027, BASE),
    ("12-zeilen-getauscht", "GuV-Zinsaufwand und Zinsertrag inklusive aller Werte vertauscht", [(2, {f"{col}13":cache[2][f"{col}12"] for col in "ABCD"} | {f"{col}12":cache[2][f"{col}13"] for col in "ABCD"})], None, 2027, 2027, BASE),
    ("13-tabellenalias", "Finanzierung heißt Darlehensspiegel", [], ("Finanzierung","Darlehensspiegel"), None, 2027, BASE),
    ("14-tabellen-tippfehler", "Finanzierung heißt Finanzerung", [], ("Finanzierung","Finanzerung"), None, 2027, BASE),
    ("15-zeilen-tippfehler", "Jahresüberschuss mit Buchstabendreher", [(2, {"A18":"Jahresübercshuss"})], None, 2027, 2027, BASE),
    ("16-zeilenalias", "Jahresüberschuss heißt Jahresergebnis", [(2, {"A18":"Jahresergebnis"})], None, 2027, 2027, BASE),
    ("17-spaltenalias", "Planmäßige Tilgung heißt Regeltilgung", [(4, {"D3":"Regeltilgung 2027"})], None, 2027, 2027, BASE),
    ("18-doppelte-zeile", "Originalzeile und leicht vertippter Doppelgänger", [(2, {"A24":"Jahresübercshuss","D24":99999})], None, 2027, None, None),
    ("19-kein-fuzzy-raten", "Kennzahl netto und brutto sind verschieden", [(2, {"A18":"Jahresüberschuss vor Steuern"})], None, 2027, None, None),
    ("20-millionen", "Darlehensspiegel vollständig in Mio. € skaliert", [(4,units(4,"Finanzierungsplan 2027 – in Mio. €",0.001))], None, 2027, 2027, BASE),
    ("21-euro", "GuV vollständig in Euro skaliert", [(2,units(2,"Erfolgsplan – in Euro",1000))], None, 2027, 2027, BASE),
    ("22-widerspruch-einheit", "Zwei widersprüchliche Einheiten in derselben GuV", [(2,{"A2":"in Mio. €"})], None, 2027, None, None),
    ("23-usd", "Darlehensspiegel in USD", [(4,{"A1":"Finanzierungsplan – in USD"})], None, 2027, None, None),
    ("24-fehlender-formelwert", "Planmäßige Gesamttilgung ohne gespeichertes Formelergebnis", [(4,{"D15":"UNCACHED"})], None, 2027, None, None),
    ("25-zahl-als-text", "Jahresüberschuss als unklar formatierter Text", [(2,{"D18":"10.700,00"})], None, 2027, None, None),
    ("26-tilgung-veraltet", "Geänderte Einzeltilgung, Summen unverändert", [(4,{"D5":8250})], None, 2027, 2027, {**BASE,"dscr":None}),
    ("27-bankschulden-widerspruch", "Darlehensspiegel und Bilanz widersprechen sich", [(4,{"F10":151000})], None, 2027, 2027, {**BASE,"leverage":None}),
    ("28-liquiditaet-gebunden", "Abweichender Vermerk zur Liquidität", [(3,{"A40":"3) Liquide Mittel: 10.000 T€ verpfändet."})], None, 2027, 2027, {**BASE,"leverage":None}),
    ("29-eigenkapital-widerspruch", "Eigenkapitalwert weicht von seiner Herleitung ab", [(3,{"D22":-2000})], None, 2027, 2027, {**BASE,"economic_equity":None,"equity":None}),
    ("30-einlagen", "25.000 T€ ausstehende eingeforderte Einlagen", [(3,{"A36":"Eingeforderte, aber noch nicht eingezahlte Einlagen","D36":25000})], None, 2027, 2027, {**BASE,"economic_equity":"26.34"}),
]

rules = ({"probe@example.org"}, "Wirtschaftsplan", "kontakt@example.org", 10)
lines = [
    "# Excel-Stresstest für Finn", "",
    "Synthetische Testkopien des Originalplans. Die XLSX-Testdateien wurden auf Paketebene variiert, "
    "damit unveränderte gespeicherte Formelwerte erhalten bleiben. Tests nutzen eine temporäre Datenbank und senden keine Mails.",
    "", "| Datei | Variante | Gewähltes Jahr | Ergebnis |", "|---|---|---|---|",
]
counts = {"gerechnet": 0, "teilweise offen": 0, "abgelehnt": 0}
with TemporaryDirectory() as directory:
    work = Path(directory)
    for name, description, edits, rename, request_year, expected_year, expected in cases:
        path = variant(name, edits, rename)
        content = path.read_bytes()
        try:
            plan = import_plan(content, path.name, request_year)
        except PlanImportError as error:
            assert expected is None, (name, error)
            outcome = str(error)
            counts["abgelehnt"] += 1
        else:
            assert expected is not None, (name, "Import hätte scheitern müssen")
            assert plan["date"] == f"{expected_year}-12-31", (name, plan["date"])
            _, rows, _, report = check_plan(plan, work)
            actual = {
                row["rule"]["id"]: None if row["value"] is None else
                f'{Decimal(row["value"]):.{len(BASE[row["rule"]["id"]].partition(".")[2])}f}'
                for row in rows if row["rule"]
            }
            assert actual == expected, (name, actual, expected)
            assert "Sparkasse" in report
            counts["teilweise offen" if None in actual.values() else "gerechnet"] += 1
            outcome = ", ".join(f"{key} {value if value is not None else 'offen'}" for key, value in actual.items())
            if name in {"02-plan-2026","30-einlagen"}:
                assert any(row["status"] == "Verletzt" for row in rows)
        if name in {"01-original","02-plan-2026","04-hr-bleibt-hr","06-mehrere-planjahre","11-alle-tabellen-verschoben"}:
            message = EmailMessage()
            message["From"] = next(iter(rules[0]))
            message["Subject"] = f"Wirtschaftsplan {request_year or ''}".strip()
            message["Message-ID"] = "<probe@example.org>"
            message.set_content("Lokaler Test")
            message.add_attachment(b"logo", maintype="image", subtype="png", disposition="inline", filename="logo.png")
            message.add_attachment(content, maintype="application", subtype="octet-stream", filename=path.name)
            replies = []
            with patch("mail_finn.ROOT", work), patch("plan_review.api_ready", return_value=False), patch(
                "mail_finn.fetch", return_value=(b"RFC822.SIZE 50000", message.as_bytes())
            ):
                process(None, 1, "finn@example.org", rules, lambda reply, _: replies.append(reply))
            assert len(replies) == 1 and replies[0]["In-Reply-To"] == "<probe@example.org>"
            assert bool(list(replies[0].iter_attachments())) == (expected is not None), name
        lines.append(f"| [{name}.xlsx]({name}.xlsx) | {description} | {expected_year or '–'} | {outcome} |")
        print(name, outcome, flush=True)

(output / "Ergebnisse.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("30 Excel-Fälle:", counts)
