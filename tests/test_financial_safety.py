"""Regression cases for misleading green results; no network or local database access."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED
import finn
from plan_excel import import_plan
from plan_review import audit_plan
ROOT = finn.ROOT
WORKBOOK = (ROOT / 'beispiele/Wirtschaftsplan_2027.xlsx').read_bytes()
def expect(condition, message='Unerwartetes Ergebnis'):
    if not condition:
        raise AssertionError(message)

def modified_workbook(sheet_number, changes, original=WORKBOOK):
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    b = BytesIO()
    with ZipFile(BytesIO(original)) as source, ZipFile(b, 'w', ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == f'xl/worksheets/sheet{sheet_number}.xml':
                tree = ET.fromstring(data)
                rows = tree.find(ns+'sheetData')
                cells = {c.get('r'): c for row in rows for c in row}
                for pos, value in changes.items():
                    c = cells.get(pos)
                    if c is None:
                        rownum = ''.join(x for x in pos if x.isdigit())
                        row = next((r for r in rows if r.get('r')==rownum), None)
                        if row is None: row = ET.SubElement(rows, ns+'row', {'r':rownum})
                        c = ET.SubElement(row, ns+'c', {'r':pos})
                    for child in list(c): c.remove(child)
                    c.attrib['t'] = 'inlineStr' if isinstance(value,str) else 'n'
                    if isinstance(value,str): ET.SubElement(ET.SubElement(c,ns+'is'),ns+'t').text=value
                    else: ET.SubElement(c,ns+'v').text=str(value)
                tree.find(ns+'dimension').set('ref','A1:G55')
                data = ET.tostring(tree)
            target.writestr(info, data)
    return b.getvalue()

def known_ai_warning_should_block():
    content = modified_workbook(3, {'D3':'Plan 2027 Stressfall'})
    plan = import_plan(content, 'Plan-mit-Szenariowiderspruch.xlsx')
    answer = {'issues':[{'field':'equity', 'quote':'Plan 2027 Stressfall', 'reason':'Bilanz verwendet Stressfall, GuV verwendet Plan 2027 ohne Stressfall. Szenarien sind nicht eindeutig identisch.'}]}
    with patch('plan_review.api_ready', return_value=True), patch('plan_review.model_json', return_value=answer):
        audit_plan(plan)
    with TemporaryDirectory() as d:
        _, results, html, _ = finn.check_plan(plan, Path(d), content)
    status = {r['rule']['id']:r['status'] for r in results if r['rule']}
    expect(status['equity']=='Nicht prüfbar', f'Belegter KI-Hinweis: {plan["mapping_audit"]}; Ergebnis: {status}; grüne Gesamtaussage: {"Alle geprüften" in html}')

def explicit_ebitda_adjustment():
    content = modified_workbook(2, {'A22':'Außerordentliche EBITDA-Bereinigungen', 'D22':-5000})
    plan = import_plan(content, 'Plan-mit-EBITDA-Bereinigung.xlsx')
    with TemporaryDirectory() as d:
        _, results, _, _ = finn.check_plan(plan, Path(d), content)
    row = next(r for r in results if r['rule'] and r['rule']['id']=='leverage')
    expect(row['status']=='Nicht prüfbar', f'Ausgewiesene Bereinigung -5.000 T€ wird zu {plan["inputs"]["extraordinary_adjustment"]}; Verschuldungsgrad bleibt {row["value"]}, statt 3,85 oder offen')

def non_subordinated_shareholder_loan():
    content = modified_workbook(3, {
        'A29': 'Verbindlichkeiten gegenüber Gesellschafter (Darlehen Stadt Musterstadt, kein Rangrücktritt)',
        'A39': '2) Gesellschafterdarlehen der Stadt Musterstadt: verzinslich (2,5 % p. a.), ohne Rangrücktritt; nicht nachrangig.',
    })
    content = modified_workbook(4, {'A13':'Gesellschafterdarlehen Stadt Musterstadt (nicht nachrangig, kein Rangrücktritt)'}, content)
    plan = import_plan(content, 'Plan-ohne-Rangruecktritt.xlsx')
    with TemporaryDirectory() as d:
        _, results, _, _ = finn.check_plan(plan, Path(d), content)
    row = next(r for r in results if r['rule'] and r['rule']['id']=='leverage')
    expect(row['status']=='Nicht prüfbar', f'8.000 T€ Gesellschafterdarlehen ohne Rangrücktritt fehlen; Ergebnis {row["value"]} statt 3,57 oder offen')

def guv_inconsistent_total():
    content = modified_workbook(2, {'D18':60700})
    plan = import_plan(content, 'Plan-mit-widerspruechlicher-GuV.xlsx')
    with TemporaryDirectory() as d:
        _, results, _, _ = finn.check_plan(plan, Path(d), content)
    row = next(r for r in results if r['rule'] and r['rule']['id']=='leverage')
    expect(row['status']=='Nicht prüfbar', f'Jahresüberschuss 60.700 trotz EBT 13.900 und Steuern -3.200: {row["value"]}, {row["status"]}')

class FinancialSafetyTests(unittest.TestCase):
    def test_explicit_ebitda_adjustment(self):
        explicit_ebitda_adjustment()

    def test_guv_inconsistent_total(self):
        guv_inconsistent_total()

    def test_known_ai_warning_should_block(self):
        known_ai_warning_should_block()

    def test_non_subordinated_shareholder_loan(self):
        non_subordinated_shareholder_loan()

    def assert_check(self, content, expected):
        plan = import_plan(content, 'Plan-Textpruefung.xlsx')
        with TemporaryDirectory() as directory:
            _, rows, _, _ = finn.check_plan(plan, Path(directory), content)
        actual = {row['rule']['id']: (row['status'], row['value']) for row in rows if row['rule']}
        for metric, result in expected.items():
            with self.subTest(metric=metric):
                self.assertEqual(actual[metric], result)

    def test_no_ebitda_adjustment_keeps_dscr_and_leverage_calculable(self):
        for changes in (
            {'A22': 'Hinweis: Keine EBITDA-Bereinigungen.'},
            {'A22': 'Keine außerordentlichen Bereinigungen.'},
            {'A22': 'Außerordentliche EBITDA-Bereinigungen', 'D22': 0},
        ):
            with self.subTest(changes=changes):
                self.assert_check(modified_workbook(2, changes), {
                    'dscr': ('Eingehalten', '1.56'), 'leverage': ('Eingehalten', '3.37'),
                })

    def test_nonzero_ebitda_adjustment_overrides_no_adjustment_note(self):
        content = modified_workbook(2, {'A22': 'Keine EBITDA-Bereinigungen.', 'D22': -5000})
        self.assert_check(content, {'dscr': ('Nicht prüfbar', None), 'leverage': ('Nicht prüfbar', None)})

    def test_no_other_financing_keeps_leverage_calculable(self):
        content = modified_workbook(3, {'A41': 'Kein Factoring.', 'A42': 'Keine Schuldscheindarlehen.'})
        self.assert_check(content, {'leverage': ('Eingehalten', '3.37')})

    def test_other_financing_amount_overrides_absence_note(self):
        for changes in (
            {'A41': 'Factoringverbindlichkeiten', 'D41': 1000},
            {'A41': 'Kein Factoring.', 'D41': 1000},
        ):
            with self.subTest(changes=changes):
                self.assert_check(modified_workbook(3, changes), {'leverage': ('Nicht prüfbar', None)})

    def test_unsecured_subordinated_loan_keeps_leverage_calculable(self):
        content = modified_workbook(4, {
            'A13': 'Gesellschafterdarlehen Stadt Musterstadt (qualifizierter Rangrücktritt, ohne Sicherheiten)',
        })
        self.assert_check(content, {'leverage': ('Eingehalten', '3.37')})

    def test_unsecured_loan_still_requires_qualified_subordination(self):
        content = modified_workbook(4, {
            'A13': 'Gesellschafterdarlehen Stadt Musterstadt (kein qualifizierter Rangrücktritt, ohne Sicherheiten)',
        })
        self.assert_check(content, {'leverage': ('Nicht prüfbar', None)})

    def test_clear_liquidity_note_accepts_harmless_rewording(self):
        for note in (
            '3) Liquide Mittel: frei verfügbar; keine Verpfändung; keine Cash-Pooling-Verbindlichkeiten',
            'Liquide Mittel sind frei verfügbar und nicht verpfändet. Es bestehen keine Cash-Pooling-Verbindlichkeiten.',
        ):
            with self.subTest(note=note):
                self.assert_check(modified_workbook(3, {'A40': note}), {'leverage': ('Eingehalten', '3.37')})

    def test_ambiguous_or_contradictory_liquidity_stays_uncheckable(self):
        for changes in (
            {'A40': 'Liquide Mittel sind frei verfügbar und nicht verpfändet.'},
            {'A41': 'Liquide Mittel sind teilweise verpfändet.'},
        ):
            with self.subTest(changes=changes):
                self.assert_check(modified_workbook(3, changes), {'leverage': ('Nicht prüfbar', None)})
