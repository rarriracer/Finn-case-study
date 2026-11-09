"""Local contract regression tests. The provider response is simulated."""

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from contracts import evidence_blocks, pdf_pages, review_candidates

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def model_rule(blocks, name, operator, threshold, unit, phrase):
    reference = next(key for key, value in blocks.items() if phrase in value["quote"])
    return {
        "name": name, "definition": name, "operator": operator, "threshold": threshold,
        "unit": unit, "rounding": None, "source_ids": [reference],
    }


class ContractImportTest(unittest.TestCase):
    def test_four_original_pdfs(self):
        examples = [
            ("Vertrag_1*", []),
            ("Vertrag_2*", [("Wirtschaftliche Eigenkapitalquote", ">=", 27.5, "%", "von mindestens 27,5 %")]),
            ("Vertrag_3*", [("DSCR", ">=", 1.3, "×", "von mindestens 1,30")]),
            ("Vertrag_4*", [
                ("Eigenkapitalquote", ">=", 30, "%", "nicht weniger als 30,0 %"),
                ("Verschuldungsgrad", "<=", 3.5, "×", "nicht mehr als 3,50"),
            ]),
        ]
        for pattern, expected in examples:
            with self.subTest(file=pattern):
                pdf = next(FIXTURES.glob(pattern))
                blocks = evidence_blocks(pdf_pages(pdf))
                rules = [model_rule(blocks, *spec) for spec in expected]
                with patch("contracts.model_json", return_value={"rules": rules}):
                    actual = review_candidates(pdf)["rules"]
                self.assertEqual([(rule["operator"], rule["threshold"]) for rule in actual],
                                 [(spec[1], spec[2]) for spec in expected])

    def test_second_rule_is_recovered_on_retry(self):
        pdf = next(FIXTURES.glob("Vertrag_4*"))
        blocks = evidence_blocks(pdf_pages(pdf))
        equity = model_rule(blocks, "Eigenkapitalquote", ">=", 30, "%", "nicht weniger als 30,0 %")
        leverage = model_rule(blocks, "Verschuldungsgrad", "<=", 3.5, "×", "nicht mehr als 3,50")
        with patch("contracts.model_json", side_effect=[
            {"rules": [deepcopy(equity)]}, {"rules": [equity, leverage]},
        ]) as model:
            result = review_candidates(pdf)
        self.assertEqual(len(result["rules"]), 2)
        self.assertEqual(model.call_count, 2)

    def test_unknown_contract_without_reference(self):
        pages = ["§ 12 Financial Covenants The minimum liquidity is at least 7 million Euro."]
        blocks = evidence_blocks(pages)
        proposal = model_rule(blocks, "Mindestliquidität", ">=", 7, "Mio. €", "at least 7")
        with patch("contracts.pdf_pages", return_value=pages), patch(
            "contracts.model_json", return_value={"rules": [proposal]}
        ):
            self.assertEqual(review_candidates(b"unknown")["rules"][0]["threshold"], 7)

    def test_rejects_wrong_direction_and_incidental_number(self):
        pages = [
            "§ 12 Financial Covenants The minimum liquidity is at least 7 million Euro.",
            "Eine unverbindliche Beispielrechnung verwendet den Wert 7.",
        ]
        for direction, phrase in (("<=", "at least 7"), (">=", "Wert 7")):
            with self.subTest(direction=direction, phrase=phrase):
                proposal = model_rule(evidence_blocks(pages), "Liquidität", direction, 7, "Mio. €", phrase)
                with patch("contracts.pdf_pages", return_value=pages), patch(
                    "contracts.model_json", side_effect=[{"rules": [deepcopy(proposal)]}, {"rules": [proposal]}],
                ):
                    with self.assertRaises(ValueError):
                        review_candidates(b"unknown")

    def test_scan_without_text_returns_clear_error(self):
        with patch("contracts.pdf_pages", return_value=[""]), patch("contracts.model_json") as model:
            with self.assertRaisesRegex(ValueError, "kein auslesbarer Vertragstext"):
                review_candidates(b"scan")
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
