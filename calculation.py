from decimal import Decimal, ROUND_HALF_UP


UNIT_FACTOR = {"€": Decimal("0.001"), "T€": Decimal(1), "Mio. €": Decimal(1000)}
EBITDA_FIELDS = (
    "net_income", "income_tax", "interest_expense", "interest_income",
    "depreciation", "extraordinary_adjustment",
)
FIELDS = {
    "economic_equity": ("equity", "unpaid_called_capital", "special_items", "assets"),
    "equity": ("equity", "assets"),
    "debt_ratio": ("liabilities", "provisions", "assets"),
    "dscr": (*EBITDA_FIELDS, "loan_interest", "scheduled_principal", "provision_interest"),
    "leverage": (*EBITDA_FIELDS, "bank_debt", "bank_debt_schedule", "cash"),
}


def normalize(entry):
    raw, unit, source = entry
    value = Decimal(str(raw)) * UNIT_FACTOR[unit]
    if not value.is_finite() or not isinstance(source, str) or not source.strip():
        raise ValueError("Zahlenwert oder Beleg fehlt")
    return value


def calculate(rule, inputs):
    values = {field: normalize(inputs[field]) for field in FIELDS[rule["id"]]}
    metric = rule["id"]
    if metric in ("economic_equity", "equity"):
        numerator = values["equity"]
        if metric == "economic_equity":
            numerator += values["special_items"] / 2 - values["unpaid_called_capital"]
        denominator = values["assets"]
        numerator *= 100
    elif metric == "debt_ratio":
        if min(values["liabilities"], values["provisions"]) < 0:
            raise ValueError("Verbindlichkeiten und Rückstellungen müssen als positive Bilanzposten vorliegen.")
        numerator = (values["liabilities"] + values["provisions"]) * 100
        denominator = values["assets"]
    else:
        ebitda = (
            values["net_income"] - values["income_tax"] - values["interest_expense"]
            - values["interest_income"] - values["depreciation"]
            + values["extraordinary_adjustment"]
        )
        if metric == "dscr":
            interest = values["loan_interest"]
            principal = values["scheduled_principal"]
            if interest != -values["interest_expense"] + values["provision_interest"]:
                raise ValueError("Darlehenszinsen stimmen nicht mit der GuV überein")
            if interest < 0 or principal < 0:
                raise ValueError("Zinsen und Tilgungen im Darlehensspiegel müssen positiv angegeben sein")
            numerator = ebitda + values["income_tax"]
            denominator = interest + principal
        else:
            if values["bank_debt"] != values["bank_debt_schedule"]:
                raise ValueError("Bankschulden in Planbilanz und Darlehensspiegel weichen ab")
            numerator = values["bank_debt"] - values["cash"]
            denominator = ebitda
    if denominator <= 0:
        raise ValueError("Nenner muss größer als null sein")
    value = numerator / denominator
    if rule["decimals"] is not None:
        value = rounded(value, rule["decimals"])
    threshold = Decimal(rule["threshold"])
    operator = rule["operator"]
    if operator not in (">=", "<=") or not threshold.is_finite():
        raise ValueError("Die freigegebene Rechenregel hat eine ungültige Vertragsgrenze.")
    headroom = value - threshold if operator == ">=" else threshold - value
    return value, headroom, numerator, denominator


def rounded(value, places):
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
