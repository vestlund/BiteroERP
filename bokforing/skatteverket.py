"""Momsdeklaration som fil till Skatteverkets e-tjänst Lämna momsdeklaration (eSKDUpload 6.0).

Format enligt Skatteverket ("Lämna momsdeklaration via fil i e-tjänsten"):
ISO-8859-1, en tagg per ruta i bestämd ordning, belopp i hela kronor, perioden som ÅÅÅÅMM för
periodens sista månad.
"""

from decimal import ROUND_DOWN, Decimal

from . import tjanster as bok

# Ruta -> XML-tagg, i den ordning taggarna ska stå i filen
TAGGAR = [
    ("05", "ForsMomsEjAnnan"), ("06", "UttagMoms"), ("07", "UlagMargbesk"), ("08", "HyrinkomstFriv"),
    ("10", "MomsUtgHog"), ("11", "MomsUtgMedel"), ("12", "MomsUtgLag"),
    ("20", "InkopVaruAnnatEg"), ("21", "InkopTjanstAnnatEg"), ("22", "InkopTjanstUtomEg"),
    ("23", "InkopVaruSverige"), ("24", "InkopTjanstSverige"),
    ("30", "MomsInkopUtgHog"), ("31", "MomsInkopUtgMedel"), ("32", "MomsInkopUtgLag"),
    ("35", "ForsVaruAnnatEg"), ("36", "ForsVaruUtomEg"), ("37", "InkopVaruMellan3p"), ("38", "ForsVaruMellan3p"),
    ("39", "ForsTjSkskAnnatEg"), ("40", "ForsTjOvrUtomEg"), ("41", "ForsKopareSkskSverige"), ("42", "ForsOvrigt"),
    ("50", "MomsUlagImport"), ("60", "MomsImportUtgHog"), ("61", "MomsImportUtgMedel"), ("62", "MomsImportUtgLag"),
    ("48", "MomsIngAvdr"),
]
UTGAENDE = ["10", "11", "12", "30", "31", "32", "60", "61", "62"]


class DeklarationsFel(Exception):
    pass


def hela_kronor(v):
    """Skatteverket vill ha hela kronor; ören stryks (avrundas mot noll)."""
    return int(Decimal(v or 0).quantize(Decimal(1), rounding=ROUND_DOWN))


def orgnr_med_streck(orgnr):
    siffror = "".join(c for c in orgnr or "" if c.isdigit())
    if len(siffror) == 12 and siffror.startswith("16"):
        siffror = siffror[2:]
    if len(siffror) != 10:
        raise DeklarationsFel("Ange organisationsnumret under Inställningar → Företagsuppgifter (10 siffror).")
    return f"{siffror[:6]}-{siffror[6:]}"


def momsrutor(fran, till):
    """Rutorna i hela kronor, med ruta 49 räknad på de avrundade beloppen så att deklarationen går ihop."""
    rutor = {r: hela_kronor(b) for r, b in bok.momsrapport(fran, till)["rutor"].items() if r != "49"}
    rutor["49"] = sum(rutor.get(r, 0) for r in UTGAENDE) - rutor.get("48", 0)
    return rutor


def momsfil(fran, till, foretag, upplysning=""):
    """Returnerar (filnamn, bytes)."""
    orgnr = orgnr_med_streck(foretag.orgnr)
    rutor = momsrutor(fran, till)
    rader = ['<?xml version="1.0" encoding="ISO-8859-1"?>', '<eSKDUpload Version="6.0">',
             f"<OrgNr>{orgnr}</OrgNr>", "<Moms>", f"<Period>{till:%Y%m}</Period>"]
    for ruta, tagg in TAGGAR:
        if rutor.get(ruta):
            rader.append(f"<{tagg}>{rutor[ruta]}</{tagg}>")
    rader.append(f"<MomsBetala>{rutor['49']}</MomsBetala>")
    if upplysning:
        from xml.sax.saxutils import escape
        rader.append(f"<TextUpplysningMoms>{escape(upplysning[:300])}</TextUpplysningMoms>")
    rader += ["</Moms>", "</eSKDUpload>"]
    data = ("\n".join(rader) + "\n").encode("iso-8859-1", errors="replace")
    return f"moms_{orgnr.replace('-', '')}_{till:%Y%m}.xml", data
