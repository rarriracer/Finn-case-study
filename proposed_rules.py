"""Build executable proposals only for explicitly supported contract language."""

from decimal import Decimal, InvalidOperation
import re

from contracts import check_source, pdf_pages, source_supports_rule


FORMULA = "(Verbindlichkeiten + Rückstellungen) / Bilanzsumme × 100"
DEFINITION = (
    "Die Fremdkapitalquote entspricht der Summe der Verbindlichkeiten und Rückstellungen "
    "geteilt durch die Bilanzsumme, multipliziert mit 100."
)
ROUNDING = "Die Kennzahl wird kaufmännisch auf eine Nachkommastelle gerundet."
PERIOD = re.compile(
    r"Prüfungsstichtag ist der 31\. Dezember jedes Geschäftsjahres während der Darlehenslaufzeit, "
    r"erstmals der 31\. Dezember (20\d{2})\."
)


def debt_ratio_proposal(content, candidates):
    rules = candidates["rules"]
    if len(rules) != 1:
        return None
    proposed = rules[0]
    if proposed["name"].strip().casefold() != "fremdkapitalquote" or proposed["operator"] != "<=" or proposed["unit"] != "%":
        return None
    try:
        threshold = Decimal(str(proposed["threshold"]).replace(",", "."))
    except InvalidOperation:
        return None
    if not threshold.is_finite() or not 0 < threshold <= 100 or threshold != threshold.quantize(Decimal("0.1")):
        return None
    pages = pdf_pages(content)
    verified_limits = []
    for source in proposed["sources"]:
        check_source(pages, source["page"], source["quote"])
        if source_supports_rule(proposed, threshold, source):
            verified_limits.append([source["page"], source["section"], source["quote"]])
    definitions = [i for i, page in enumerate(pages, 1) if DEFINITION in page]
    roundings = [i for i, page in enumerate(pages, 1) if ROUNDING in page]
    periods = [(i, match) for i, page in enumerate(pages, 1) for match in PERIOD.finditer(page)]
    if not verified_limits or len(definitions) != 1 or len(roundings) != 1 or len(periods) != 1:
        return None
    page, period = periods[0]
    return {
        "id": "debt_ratio", "name": "Fremdkapitalquote", "operator": "<=", "threshold": str(threshold),
        "unit": "%", "decimals": 1, "formula": FORMULA, "first_as_of": f"{period[1]}-12-31",
        "sources": [
            *verified_limits,
            [definitions[0], "Definition", DEFINITION],
            [roundings[0], "Rundung", ROUNDING],
            [page, "Erster Prüfungsstichtag", period[0]],
        ],
    }
