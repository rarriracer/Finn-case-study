"""Contract consequences for the reviewed case-study rules."""

CONSEQUENCES = {
    "economic_equity": (
        "Förderbank",
        "Bei einer Planunterschreitung sind unverzüglich Gespräche über geeignete Maßnahmen vorgesehen (§ 13 Abs. 3, Seite 6).",
        "Die Planabweichung allein ist kein Kündigungsgrund. Bei einer tatsächlichen Unterschreitung am Bilanzstichtag kann die Bank angemessene Sicherheit verlangen. Wird die verlangte Sicherheit nicht innerhalb von 60 Tagen nach Aufforderung gestellt, kann die Bank kündigen (§ 13 Abs. 3–4 und § 15 Abs. 1 lit. d, Seiten 6–7).",
    ),
    "dscr": (
        "Landesbank",
        "Wird die Planrechnung mit einem DSCR unter 1,30× der Bank übermittelt, sind ihr innerhalb von 30 Tagen die vorgesehenen Maßnahmen darzulegen (§ 13 Abs. 3, Seite 6).",
        "Bei einer tatsächlichen Unterschreitung kann eine ausreichende Eigenkapitalzuführung innerhalb von 30 Tagen nach Vorlage der Kennzahlenbescheinigung die Verletzung heilen. Die Heilung ist höchstens zweimal während der Laufzeit und nicht in aufeinanderfolgenden Geschäftsjahren zulässig. Ohne Heilung besteht ein Kündigungsgrund (§ 13 Abs. 4 und § 15 Abs. 1 lit. b, Seiten 6–7).",
    ),
    "equity": (
        "Konsortialkredit",
        "Auf Verlangen der Mehrheitskreditgeber ist ein Gespräch über Gegenmaßnahmen zu führen (§ 14 Abs. 3, Seite 7).",
        "Die Planabweichung allein ist kein Kündigungsgrund. Eine tatsächliche Grenzverletzung am Prüfungsstichtag ist ein Kündigungsgrund. Eine Eigenkapitalzuführung nach dem Stichtag heilt sie nicht. Ein schriftlicher Verzicht mit der erforderlichen Zustimmung gilt nur für den betreffenden Prüfungsstichtag (§ 14 Abs. 3–4, § 16 Abs. 1 lit. b und § 19, Seiten 7 und 9).",
    ),
}
CONSEQUENCES["leverage"] = (
    *CONSEQUENCES["equity"][:2],
    CONSEQUENCES["equity"][2]
    + " Das separate Margenraster dient der Preisbestimmung anhand der Compliance-Bescheinigung (§ 6 Abs. 3–4, Seite 4).",
)


def consequence(result):
    if result["status"] == "Verletzt" and result["rule"]:
        return CONSEQUENCES.get(result["rule"]["id"])
    return None
