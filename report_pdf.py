"""Build a printable PDF from the same verified results used in the HTML report."""

from html import escape
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate

from covenant_notices import consequence
from presentation import BET_RED, calculation_detail, order, plan_input_line, values


def make_report_pdf(plan, results, run_id, coverage):
    buffer = BytesIO()
    ink, red, muted = map(HexColor, ("#292329", BET_RED, "#514850"))
    styles = {
        "title": ParagraphStyle("Title", fontName="Helvetica-Bold", fontSize=25, leading=30, textColor=ink, spaceAfter=8),
        "heading": ParagraphStyle("Heading", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=red, spaceBefore=11, spaceAfter=4, keepWithNext=True),
        "label": ParagraphStyle("Label", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=ink, spaceBefore=4, spaceAfter=2, keepWithNext=True),
        "body": ParagraphStyle("Body", fontSize=10, leading=12, textColor=ink, spaceAfter=2),
    }
    story = []

    def add(style, text):
        story.append(Paragraph(escape(str(text)), styles[style]))

    def footer(canvas, doc):
        canvas.setStrokeColor(HexColor("#e2d8dd"))
        canvas.line(52, 45, A4[0] - 52, 45)
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(52, 31, f"Finn · Prüflauf {run_id}")
        canvas.drawRightString(A4[0] - 52, 31, f"Seite {doc.page}")

    add("title", "Finn · Prüfbericht")
    add("body", f'{plan["date"]} · {plan["name"]}')
    add("body", "Beträge im Rechenweg in T€.")
    if coverage:
        add("body", coverage)
    if audit := plan.get("mapping_audit"):
        add("body", f'KI-Abgleich · {audit["status"]}')
        for note in audit.get("observations", []):
            add("body", note)

    for result in sorted((item for item in results if item["rule"]), key=order):
        start = len(story)
        rule = result["rule"]
        actual, limit, distance = values(result)
        add("heading", f'{result["bank"]} · {rule["name"]}')
        add("body", " · ".join(part for part in (result["status"], actual, limit, distance) if part))
        add("label", "Rechenweg")
        add("body", rule["formula"])
        add("body", calculation_detail(result, actual))
        add("label", "Vertragsbelege")
        add("body", result["filename"])
        for page, section, quote in rule["sources"]:
            add("body", f'Seite {page}, {section} · „{quote}“')
        if result["inputs"]:
            add("label", "Planwerte und Fundstellen")
            for field, entry in result["inputs"].items():
                add("body", plan_input_line(field, entry))
        if notice := consequence(result):
            add("label", "Vertragliche Folgen")
            add("body", f"Planabweichung · {notice[1]}")
            add("body", f"Tatsächliche Verletzung · {notice[2]}")
        story[start:] = [KeepTogether(story[start:])]

    without_rule = [item["bank"] for item in results if not item["rule"]]
    if without_rule:
        add("heading", "Verträge ohne Kennzahlengrenze")
        add("body", ", ".join(without_rule))
    SimpleDocTemplate(buffer, pagesize=A4, topMargin=52, bottomMargin=63, leftMargin=52, rightMargin=52,
                      title="Finn · Prüfbericht", author="Finn").build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
