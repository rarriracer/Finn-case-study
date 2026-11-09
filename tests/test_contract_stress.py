"""Stress the boundary between a model proposal and an active calculation rule."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from contract_mail import confirm, connect, matched_reference, review
from contracts import review_candidates
from finn import FIXTURES, digest, reference_contracts


def proposal(name, operator, threshold, unit, sources=("S1-1",)):
    return {
        "name": name, "operator": operator, "threshold": threshold, "unit": unit,
        "definition": name, "rounding": None, "source_ids": list(sources),
    }


VALID = [
    ("Fremdkapitalquote höchstens 70 %.", "Fremdkapitalquote", "<=", 70, "%"),
    ("Eigenkapitalquote mindestens 30 %.", "Eigenkapitalquote", ">=", 30, "%"),
    ("Wirtschaftliche Eigenkapitalquote mindestens 27,5 Prozent.", "Wirtschaftliche Eigenkapitalquote", ">=", "27,5", "%"),
    ("Zinsdeckungsgrad mindestens 2,5 fach.", "Zinsdeckungsgrad", ">=", 2.5, "×"),
    ("Mindestliquidität mindestens 7 Millionen Euro.", "Mindestliquidität", ">=", 7, "Mio. €"),
    ("Interest Coverage Ratio at least 2.5.", "Interest Coverage Ratio", ">=", 2.5, "×"),
    ("Investitionsquote maximal 18 Prozent.", "Investitionsquote", "<=", 18, "%"),
    ("Verschuldungsgrad ≤ 3,5.", "Verschuldungsgrad", "<=", 3.5, "×"),
    ("EBITDA-Marge > 5 %.", "EBITDA-Marge", ">", 5, "%"),
    ("Zinsaufwand höchstens 250 Tausend Euro.", "Zinsaufwand", "<=", 250, "T€"),
    ("Eigenkapitalquote mindestens 0 %.", "Eigenkapitalquote", ">=", 0, "%"),
    ("Jahresergebnis mindestens -5 Millionen Euro.", "Jahresergebnis", ">=", -5, "Mio. €"),
    ("Kurzfristige Liquiditätsquote mindestens 110 %.", "Kurzfristige Liquiditätsquote", ">=", 110, "%"),
    ("EBIT-Marge mindestens 8 %.", "EBIT-Marge", ">=", 8, "%"),
    ("Bereinigter Cashflow mindestens 8 Mio. EUR.", "Bereinigter Cashflow", ">=", 8, "Mio. €"),
]

INVALID = [
    ("Fremdkapitalquote höchstens 70 %.", "Eigenkapitalquote", "<=", 70, "%"),
    ("Eigenkapitalquote mindestens 30 %.", "Fremdkapitalquote", ">=", 30, "%"),
    ("Wirtschaftliche Eigenkapitalquote mindestens 30 %.", "Eigenkapitalquote", ">=", 30, "%"),
    ("Eigenkapitalquote mindestens 30 %.", "Wirtschaftliche Eigenkapitalquote", ">=", 30, "%"),
    ("Eigenkapitalquote mindestens 30 %.", "Eigenkapitalquote", "<=", 30, "%"),
    ("Eigenkapitalquote mindestens 30 %.", "Eigenkapitalquote", ">", 30, "%"),
    ("Eigenkapitalquote mindestens 30 %.", "Eigenkapitalquote", ">=", 30, "×"),
    ("Mindestliquidität mindestens 7 Millionen Euro.", "Mindestliquidität", ">=", 7, "€"),
    ("Mindestliquidität mindestens 7 Millionen Euro.", "Mindestliquidität", ">=", 7, "T€"),
    ("Jahresergebnis mindestens -5 Millionen Euro.", "Jahresergebnis", ">=", 5, "Mio. €"),
    ("Eigenkapitalquote mindestens 30 %.", "Eigenkapitalquote", ">=", 31, "%"),
    ("Eigenkapitalquote mindestens 30 %. Eine Beispielrechnung verwendet 40 %.", "Eigenkapitalquote", ">=", 40, "%"),
    ("Historisch betrug die Eigenkapitalquote mindestens 30 %.", "Eigenkapitalquote", ">=", 30, "%"),
    ("Eigenkapitalquote mindestens 30 % und Fremdkapitalquote höchstens 70 %.", "Eigenkapitalquote", "<=", 70, "%"),
    ("Mindestliquidität mindestens 7 Millionen Euro.", "Mindestliquidität", "=", 7, "Mio. €"),
    ("Eigenkapitalquote mindestens 30 %.", "Eigenkapitalquote", ">=", "NaN", "%"),
]


class ContractStressTest(unittest.TestCase):
    def test_new_supported_text_patterns(self):
        for clause, name, operator, threshold, unit in VALID:
            with self.subTest(clause=clause):
                rule = proposal(name, operator, threshold, unit)
                with patch("contracts.pdf_pages", return_value=[f"§ 12 Finanzkennzahlen {clause}"]), patch(
                    "contracts.model_json", return_value={"rules": [rule]},
                ):
                    result = review_candidates(b"synthetic PDF")
                self.assertEqual(result["rules"][0]["name"], name)

    def test_model_mistakes_do_not_pass_as_verified(self):
        for clause, name, operator, threshold, unit in INVALID:
            with self.subTest(clause=clause, proposal=(name, operator, threshold, unit)):
                rule = proposal(name, operator, threshold, unit)
                with patch("contracts.pdf_pages", return_value=[f"§ 12 Finanzkennzahlen {clause}"]), patch(
                    "contracts.model_json", side_effect=[{"rules": [deepcopy(rule)]}, {"rules": [rule]}],
                ):
                    with self.assertRaises(ValueError):
                        review_candidates(b"synthetic PDF")

    def test_same_threshold_for_two_distinct_metrics_requires_both(self):
        pages = ["§ 12 Finanzkennzahlen Die Eigenkapitalquote beträgt mindestens 30 %. "
                 "Die Liquiditätsquote beträgt mindestens 30 %."]
        for second in ([], [proposal("Bilanzielle Eigenkapitalquote", ">=", 30, "%")]):
            first = proposal("Eigenkapitalquote", ">=", 30, "%")
            wrong = {"rules": [first, *second]}
            correct = {"rules": [deepcopy(first), proposal("Liquiditätsquote", ">=", 30, "%")]}
            with self.subTest(second=second), patch("contracts.pdf_pages", return_value=pages), patch(
                "contracts.model_json", side_effect=[wrong, correct],
            ):
                self.assertEqual(len(review_candidates(b"synthetic PDF")["rules"]), 2)

    def test_incomplete_scan_does_not_pass(self):
        with patch("contracts.pdf_pages", return_value=["§ 12 Finanzkennzahlen", ""]), patch("contracts.model_json") as model:
            with self.assertRaisesRegex(ValueError, "keinen auslesbaren Text"):
                review_candidates(b"synthetic PDF")
        model.assert_not_called()

    def test_known_pdf_with_wrong_metric_cannot_activate(self):
        contract = next(item for item in reference_contracts() if item["id"] == "konsortium")
        pdf_hash = digest((FIXTURES / contract["file"]).read_bytes())
        valid = {"rules": [proposal("Eigenkapitalquote", ">=", 30, "%"), proposal("Verschuldungsgrad", "<=", 3.5, "×")]}
        wrong = deepcopy(valid)
        wrong["rules"][0]["name"] = "Fremdkapitalquote"
        self.assertIsNotNone(matched_reference(pdf_hash, valid))
        self.assertIsNone(matched_reference(pdf_hash, wrong))

    def test_unknown_pdf_can_be_reviewed_but_not_activated(self):
        pages = ["§ 12 Finanzkennzahlen Die Fremdkapitalquote beträgt höchstens 70 %."]
        rule = proposal("Fremdkapitalquote", "<=", 70, "%")
        with TemporaryDirectory() as directory, patch("contracts.pdf_pages", return_value=pages), patch(
            "contracts.model_json", return_value={"rules": [rule]},
        ), patch("proposed_rules.pdf_pages", return_value=pages), patch("contract_mail.api_ready", return_value=True):
            output = Path(directory)
            title, message = review(output, b"%PDF-synthetic", "neu.pdf", "reviewer@example.org")
            self.assertIn("zur Bestätigung", title)
            self.assertIn("Fremdkapitalquote", message)
            self.assertIn("keine passende geprüfte Rechenregel", message)
            with connect(output) as db:
                identifier, data = db.execute("SELECT id, proposal FROM contract_reviews").fetchone()
                self.assertEqual(json.loads(data)["rules"][0]["threshold"], 70)
            self.assertEqual(confirm(output, identifier, "other@example.org", True)[0], "Prüfentwurf nicht gefunden")
            self.assertEqual(confirm(output, identifier, "reviewer@example.org", True)[0], "Bestätigung gespeichert")
            with connect(output) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM active_contracts").fetchone()[0], 0)
                self.assertTrue(db.execute("SELECT confirmed_at IS NOT NULL FROM contract_reviews").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
