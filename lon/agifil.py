"""Arbetsgivardeklaration på individnivå som XML-fil till Skatteverkets e-tjänst.

Följer Skatteverkets XML-schema version 1.1 (teknisk beskrivning 1.1.18) och strukturbilagan:
Skatteverket > Avsandare, Blankettgemensamt och en Blankett per huvuduppgift (HU) och individuppgift (IU).
Belopp i hela kronor. Testa gärna filen i Skatteverkets tjänst "Testa arbetsgivardeklaration" först.
"""

from decimal import ROUND_DOWN, Decimal
from xml.sax.saxutils import escape

from django.utils import timezone

from config.version import VERSION

from . import tjanster as lon

INSTANS = "http://xmls.skatteverket.se/se/skatteverket/da/instans/schema/1.1"
KOMPONENT = "http://xmls.skatteverket.se/se/skatteverket/da/komponent/schema/1.1"
SCHEMA = "http://xmls.skatteverket.se/se/skatteverket/da/arbetsgivardeklaration/arbetsgivardeklaration_1.1.xsd"


class AgiFel(Exception):
    pass


def kr(v):
    return int(Decimal(v or 0).quantize(Decimal(1), rounding=ROUND_DOWN))


def ag_id(orgnr):
    """Organisationsnummer med sekelsiffrorna 16 för juridiska personer, t.ex. 165569744302."""
    s = "".join(c for c in orgnr or "" if c.isdigit())
    if len(s) == 10:
        s = "16" + s
    if len(s) != 12:
        raise AgiFel("Ange organisationsnumret under Inställningar → Företagsuppgifter.")
    return s


def personnummer(anstalld):
    s = "".join(c for c in anstalld.personnummer if c.isdigit())
    if len(s) == 10 and anstalld.fodelsear:
        s = str(anstalld.fodelsear)[:2] + s
    if len(s) != 12:
        raise AgiFel(f"Personnumret för {anstalld.namn} ska ha formatet ÅÅÅÅMMDD-XXXX.")
    return s


def _e(namn, varde, faltkod=None):
    attr = f' faltkod="{faltkod}"' if faltkod else ""
    return f"<agd:{namn}{attr}>{escape(str(varde))}</agd:{namn}>"


def agi_xml(period, foretag):
    """period 'ÅÅÅÅ-MM' (utbetalningsmånad). Returnerar (filnamn, bytes, sammanfattning)."""
    data = lon.agi(period)
    if not data["individer"]:
        raise AgiFel(f"Det finns inga bokförda löner med utbetalning i {period}.")
    namn = (foretag.var_referens or "").strip()
    if not namn or not foretag.telefon or not foretag.epost:
        raise AgiFel("Fyll i vår referens (kontaktperson), telefon och e-post under Inställningar → "
                     "Företagsuppgifter. Skatteverket kräver en kontaktperson i filen.")
    agid = ag_id(foretag.orgnr)
    per = period.replace("-", "")
    kontakt = [_e("Namn", namn[:50]), _e("Telefon", foretag.telefon[:20]), _e("Epostadress", foretag.epost)]

    x = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
         f'<Skatteverket xmlns="{INSTANS}" xmlns:agd="{KOMPONENT}" '
         f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
         f'xsi:schemaLocation="{INSTANS} {SCHEMA}" omrade="Arbetsgivardeklaration">',
         "<agd:Avsandare>", _e("Programnamn", f"Bitero ERP {VERSION}"[:50]), _e("Organisationsnummer", agid),
         "<agd:TekniskKontaktperson>", *kontakt, "</agd:TekniskKontaktperson>",
         _e("Skapad", f"{timezone.localtime():%Y-%m-%dT%H:%M:%S}"), "</agd:Avsandare>",
         "<agd:Blankettgemensamt>", "<agd:Arbetsgivare>", _e("AgRegistreradId", agid),
         "<agd:Kontaktperson>", *kontakt, "</agd:Kontaktperson>", "</agd:Arbetsgivare>", "</agd:Blankettgemensamt>"]

    def blankett(innehall):
        return ["<agd:Blankett>", "<agd:Arendeinformation>", _e("Arendeagare", agid), _e("Period", per),
                "</agd:Arendeinformation>", "<agd:Blankettinnehall>", *innehall, "</agd:Blankettinnehall>",
                "</agd:Blankett>"]

    summa_avgift = kr(data["summa_avgift"])
    summa_skatt = sum(kr(i["001"]) for i in data["individer"].values())
    x += blankett(["<agd:HU>", "<agd:ArbetsgivareHUGROUP>", _e("AgRegistreradId", agid, "201"),
                   "</agd:ArbetsgivareHUGROUP>", _e("RedovisningsPeriod", per, "006"),
                   _e("SummaArbAvgSlf", summa_avgift, "487"), _e("SummaSkatteavdr", summa_skatt, "497"),
                   "</agd:HU>"])
    for anstalld, i in sorted(data["individer"].items(), key=lambda p: p[0].nummer):
        iu = ["<agd:IU>", "<agd:ArbetsgivareIUGROUP>", _e("AgRegistreradId", agid, "201"), "</agd:ArbetsgivareIUGROUP>",
              "<agd:BetalningsmottagareIUGROUP>", "<agd:BetalningsmottagareIDChoice>",
              _e("BetalningsmottagarId", personnummer(anstalld), "215"), "</agd:BetalningsmottagareIDChoice>",
              "</agd:BetalningsmottagareIUGROUP>",
              _e("RedovisningsPeriod", per, "006"), _e("Specifikationsnummer", anstalld.nummer, "570")]
        for falt, ruta in (("KontantErsattningUlagAG", "011"), ("SkatteplOvrigaFormanerUlagAG", "012"),
                           ("SkatteplBilformanUlagAG", "013")):
            if kr(i[ruta]):
                iu.append(_e(falt, kr(i[ruta]), ruta))
        iu.append(_e("AvdrPrelSkatt", kr(i["001"]), "001"))  # exakt ett skattefält måste finnas
        iu.append("</agd:IU>")
        x += blankett(iu)
    x.append("</Skatteverket>")
    filnamn = f"agi_{agid}_{per}.xml"
    return filnamn, ("\n".join(x) + "\n").encode("utf-8"), {
        "individer": len(data["individer"]), "avgift": summa_avgift, "skatt": summa_skatt}
