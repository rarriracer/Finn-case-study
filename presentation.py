from decimal import Decimal
from html import escape

from calculation import rounded
from covenant_notices import consequence


BET_RED = "#E30014"
BET_GRADIENT_ROW = (
    '<tr><td height="4" bgcolor="#E30014" style="height:4px;line-height:4px;font-size:0;'
    'background-color:#E30014;background-image:linear-gradient(to right,#BC1955 0%,#E30014 50%,#FF6400 100%)">'
    '&nbsp;</td></tr>'
)

PLAN_INPUT_LABELS = {
    "net_income": "Jahresüberschuss",
    "income_tax": "Steuern vom Einkommen und vom Ertrag",
    "interest_expense": "Zinsen und ähnliche Aufwendungen",
    "interest_income": "Sonstige Zinsen und ähnliche Erträge",
    "depreciation": "Abschreibungen",
    "extraordinary_adjustment": "Außerordentliche EBITDA-Bereinigungen",
    "equity": "Bilanzielles Eigenkapital",
    "unpaid_called_capital": "Eingeforderte, noch nicht eingezahlte Einlagen",
    "special_items": "Sonderposten für Investitions- und Baukostenzuschüsse",
    "assets": "Bilanzsumme",
    "liabilities": "Verbindlichkeiten",
    "provisions": "Rückstellungen",
    "loan_interest": "Darlehenszinsen",
    "scheduled_principal": "Planmäßige Tilgung",
    "provision_interest": "Aufzinsung der Pensionsrückstellungen",
    "bank_debt": "Bankverbindlichkeiten laut Bilanz",
    "bank_debt_schedule": "Bankverbindlichkeiten laut Darlehensspiegel",
    "cash": "Frei verfügbare liquide Mittel",
}


def fmt(value, places):
    return f"{rounded(Decimal(value), places):.{places}f}".replace(".", ",")


def order(result):
    if result["headroom"] is None:
        return (0, Decimal(0))
    return (1, Decimal(result["headroom"]) / Decimal(result["rule"]["threshold"]))


def values(result):
    rule = result["rule"]
    places = rule["decimals"] if rule["decimals"] is not None else 2
    unit = rule["unit"]
    boundary = "mindestens" if rule["operator"] == ">=" else "höchstens"
    limit = f'{boundary} {fmt(rule["threshold"], places)} {unit}'
    if result["value"] is None:
        return "Offen", limit, ""
    actual = f'{fmt(result["value"], places)} {unit}'
    gap = Decimal(result["headroom"])
    distance_unit = "Prozentpunkte" if unit == "%" else "×"
    distance = f"{fmt(abs(gap), places)} {distance_unit}"
    if gap < 0:
        distance += " außerhalb der Grenze"
    elif gap > 0:
        distance += " Spielraum"
    else:
        distance = "Genau an der Grenze"
    return actual, limit, distance


def format_amount(value):
    whole, _, decimals = f"{Decimal(str(value)):,f}".partition(".")
    whole = whole.replace(",", ".")
    decimals = decimals.rstrip("0")
    return f"{whole},{decimals}" if decimals else whole


def plan_input_line(field, entry):
    amount, unit, source = entry
    return f"{PLAN_INPUT_LABELS[field]} · {format_amount(amount)} {unit} · {source}"


def calculation_detail(result, actual):
    if result["value"] is None:
        error = result["error"]
        missing = PLAN_INPUT_LABELS.get(error.strip("'"))
        return f"Der Planwert für {missing} fehlt." if missing else error
    numerator = Decimal(result["numerator"])
    percent = result["rule"]["unit"] == "%"
    if percent:
        numerator = numerator.scaleb(-2)
    multiplier = " × 100" if percent else ""
    denominator = format_amount(result["denominator"])
    return f"{format_amount(numerator)} / {denominator}{multiplier} = {actual}"


def document(title, body):
    return f"""<!doctype html>
<html lang="de">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;background:#f8f7f4;color:#262220;font:16px/1.55 Arial,sans-serif">
{body}
</body>
</html>
"""


def mail_row(result):
    actual, limit, distance = values(result)
    rule = result["rule"]
    status = {
        "Eingehalten": "Eingehalten",
        "Verletzt": "Grenze verfehlt",
        "Nicht prüfbar": "Prüfung offen",
    }[result["status"]]
    status_color = {
        "Eingehalten": "#14502c",
        "Verletzt": "#9b1c2c",
        "Nicht prüfbar": "#855000",
    }[result["status"]]
    context = f"Vertraglich {limit}"
    if distance:
        context += f"<br>{escape(distance)}"
    return f"""
<tr>
  <td style="padding:16px 12px 16px 0;border-top:1px solid #ddd6d0;vertical-align:top">
    <span style="font-size:13px;color:#665953">{escape(result["bank"])}</span><br>
    <strong>{escape(rule["name"])}</strong><br>
    <strong style="font-size:13px;color:{status_color}">{status}</strong>
  </td>
  <td style="padding:16px 0;border-top:1px solid #ddd6d0;text-align:right;vertical-align:top">
    <strong style="font-size:19px;white-space:nowrap">{escape(actual)}</strong><br>
    <span style="font-size:13px;color:#665953">{context}</span>
  </td>
</tr>"""


def render_mail(plan, checked, other_banks, coverage):
    open_count = sum(r["status"] == "Nicht prüfbar" for r in checked)
    failed = sum(r["status"] == "Verletzt" for r in checked)
    year = plan["date"].split("-")[0]
    date = ".".join(reversed(plan["date"].split("-")))
    if failed:
        headline = "Der Plan verfehlt eine Vertragsgrenze" if failed == 1 else "Der Plan verfehlt mehrere Vertragsgrenzen"
    elif open_count:
        headline = "Die Prüfung ist noch offen"
    elif checked:
        headline = "Alle geprüften Financial Covenants werden auf Planbasis eingehalten"
    else:
        headline = "Für diesen Plan gelten keine Kennzahlengrenzen"

    if open_count:
        introduction = (
            "Für eine Kennzahl fehlen eindeutige Angaben. Im Prüfbericht steht, welche Angabe wir noch brauchen."
            if open_count == 1 else
            f"Für {open_count} Kennzahlen fehlen eindeutige Angaben. Im Prüfbericht steht, welche Angaben wir noch brauchen."
        )
    elif checked and not failed:
        closest = checked[0]
        actual, limit, distance = values(closest)
        metric = escape(closest["rule"]["name"])
        bank = escape(closest["bank"])
        margin = f" Der Spielraum beträgt {escape(distance.removesuffix(' Spielraum'))}." if Decimal(closest["headroom"]) > 0 else ""
        introduction = (
            f"Am nächsten an ihrer Vertragsgrenze liegt die Kennzahl <strong>{metric}</strong> "
            f"im {bank}." if bank == "Konsortialkredit" else
            f"Am nächsten an ihrer Vertragsgrenze liegt die Kennzahl <strong>{metric}</strong> "
            f"bei der {bank}."
        )
        introduction += f" Im Plan steht sie bei {escape(actual)}, vereinbart sind {escape(limit)}.{margin}"
    elif failed:
        introduction = "Die betroffenen Kennzahlen stehen oben in der Übersicht. Dort sehen Sie den Planwert und den Abstand zur Vertragsgrenze."
    else:
        introduction = "Ich habe die hinterlegten Kreditverträge auf Finanzkennzahlen geprüft."

    coverage_html = f'<p style="margin:0 0 12px"><strong>{escape(coverage)}</strong></p>' if coverage else ""
    scope = ""
    if other_banks:
        banks = ", ".join(escape(bank) for bank in other_banks)
        scope = f'<p style="margin:14px 0 0;color:#665953;font-size:13px">Verträge ohne Kennzahlengrenze im Prüfbestand: {banks}.</p>'
    body = f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">
<tr><td align="center" style="padding:24px 14px">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;text-align:left">
    <tr><td style="padding:0 0 14px;border-bottom:3px solid {BET_RED}">
      <strong style="font-size:20px;color:{BET_RED}">Finn</strong>
      <span style="padding-left:9px;color:#665953;font-size:13px">Finanzierung</span>
    </td></tr>
    {BET_GRADIENT_ROW}
    <tr><td style="padding:22px 0 20px">
      <p style="margin:0;color:#665953;font-size:13px">Wirtschaftsplan {escape(year)} · Stichtag {escape(date)}</p>
      <h1 style="margin:8px 0 18px;font:bold 31px/1.15 Georgia,serif;color:#2e2522">{headline}</h1>
      {coverage_html}
      <p style="margin:0 0 10px">Guten Tag,</p>
      <p style="margin:0">{introduction}</p>
    </td></tr>
    <tr><td>
      <h2 style="margin:0 0 8px;font-size:16px">Die Kennzahlen im Überblick</h2>
      <table width="100%" cellspacing="0" cellpadding="0" aria-label="Kennzahlen und Vertragsgrenzen">
        <thead><tr>
          <th scope="col" style="padding:0 8px 5px 0;text-align:left;font-size:13px;color:#665953">Kennzahl</th>
          <th scope="col" style="padding:0 0 5px;text-align:right;font-size:13px;color:#665953">Planwert und Grenze</th>
        </tr></thead>
        <tbody>{"".join(mail_row(result) for result in checked)}</tbody>
      </table>
      {scope}
    </td></tr>
    <tr><td style="padding:22px 0 0">
      <p style="margin:0 0 15px;color:#665953;font-size:13px">Grundlage sind die Kreditverträge und Ihr Wirtschaftsplan {escape(year)}. Rechenwege und Fundstellen stehen im angehängten Prüfbericht.</p>
      <p style="margin:0">Viele Grüße<br><strong>Finn</strong></p>
    </td></tr>
  </table>
</td></tr>
</table>
"""
    return document(f"Finn · Wirtschaftsplan {escape(year)}", body)


def render_report(plan, checked, other_banks, run_id, coverage):
    sections = []
    for result in checked:
        rule = result["rule"]
        actual, limit, distance = values(result)
        sources = "".join(
            f"<li>{escape(plan_input_line(field, entry))}</li>"
            for field, entry in result["inputs"].items()
        )
        quotes = "".join(
            f"<li>Seite {page} · {escape(section)} · „{escape(quote)}“</li>"
            for page, section, quote in rule["sources"]
        )
        detail = escape(calculation_detail(result, actual))
        contract_details = ""
        if notice := consequence(result):
            contract_details = (
                f"<h3>Vertragliche Folgen</h3><p><strong>Planabweichung</strong><br>{escape(notice[1])}</p>"
                f"<p><strong>Tatsächliche Verletzung</strong><br>{escape(notice[2])}</p>"
            )
        sections.append(
            f'<section><h2>{escape(rule["name"])}</h2>'
            f'<p>{escape(result["status"])} · {escape(limit)} · {escape(distance)}</p>'
            f'<p>{escape(rule["formula"])}<br>{detail}</p>'
            f'<h3>Vertrag</h3><p>{escape(result["filename"])}</p><ul>{quotes}</ul>'
            f'<h3>Planwerte</h3><ul>{sources}</ul>{contract_details}</section>'
        )
    scope = escape(", ".join(other_banks))
    audit = plan.get("mapping_audit")
    audit_note = ""
    if audit:
        observations = "".join(f"<li>{escape(note)}</li>" for note in audit.get("observations", []))
        audit_note = f'<p>KI-Abgleich · {escape(audit["status"])}</p>'
        if observations:
            audit_note += f"<p>KI-Hinweise zur fachlichen Prüfung</p><ul>{observations}</ul>"
    return document("Finn · Prüfbericht", f"""
<main style="max-width:760px;margin:0 auto;padding:24px">
<h1>Prüfbericht</h1><p>{escape(plan["date"])} · {escape(plan["name"])}</p>
<p>Beträge im Rechenweg in T€.</p>
<p>{escape(coverage)}</p>
{audit_note}
{"".join(sections)}
<p>Verträge ohne Kennzahlengrenze im Prüfbestand: {scope or "keine"}.</p>
<p>Prüflauf {run_id}</p>
</main>
""")


def render(plan, results, run_id, coverage=""):
    checked = sorted((r for r in results if r["rule"]), key=order)
    other_banks = [r["bank"] for r in results if not r["rule"]]
    return render_mail(plan, checked, other_banks, coverage), render_report(plan, checked, other_banks, run_id, coverage)
