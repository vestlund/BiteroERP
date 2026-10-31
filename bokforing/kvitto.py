"""Läser av leverantörsfakturor och kvitton i PDF-format och föreslår uppgifterna.

Fungerar för PDF:er med text (de flesta e-fakturor och digitala kvitton). Fotograferade eller skannade
kvitton innehåller ingen text och kan inte läsas av – då fyller du i uppgifterna själv.
"""

import datetime as dt
import io
import re
from decimal import Decimal, InvalidOperation

from .betalfil import _ocr_giltig

BELOPP = r"(-?\d{1,3}(?:[  .]\d{3})+(?:[.,]\d{1,2})?|-?\d+(?:[.,]\d{1,2})?)"
DATUM = r"(\d{4}-\d{2}-\d{2}|\d{4}\.\d{2}\.\d{2}|\d{2}[./-]\d{2}[./-]\d{4}|\d{8}|\d{4}/\d{2}/\d{2})"

TOTAL_ORD = [r"summa att betala", r"att betala(?: sek| kr)?", r"belopp att betala", r"total(?:t|summa)?(?: inkl\.? moms)?",
             r"summa(?: inkl\.? moms)?", r"amount due", r"total amount"]
MOMS_ORD = [r"varav moms", r"moms(?:belopp)?(?: totalt)?", r"mervärdesskatt", r"vat", r"mva"]


class TolkningsFel(Exception):
    pass


def text_ur_pdf(data):
    import logging

    from pypdf import PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        lasare = PdfReader(io.BytesIO(data))
        return "\n".join((s.extract_text() or "") for s in lasare.pages[:4])
    except Exception as e:  # trasig eller krypterad PDF
        raise TolkningsFel(f"PDF:en gick inte att läsa ({e.__class__.__name__}).")


def tal(s):
    """'1 234,50', '1.234,50', '1,234.50', '1234.5' -> Decimal."""
    s = s.replace("\u00a0", "").replace(" ", "").strip()
    sista = max(s.rfind(","), s.rfind("."))
    if sista >= 0 and len(s) - sista - 1 in (1, 2):
        heltal, dec = s[:sista], s[sista + 1:]
    else:
        heltal, dec = s, "0"
    heltal = heltal.replace(",", "").replace(".", "")
    try:
        return Decimal(f"{heltal}.{dec}")
    except InvalidOperation:
        return None


def datum(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y%m%d", "%Y/%m/%d"):
        try:
            d = dt.datetime.strptime(s, fmt).date()
            if 2000 <= d.year <= 2100:
                return d
        except ValueError:
            continue
    return None


def _efter(text, ord_lista, monster, rader_framat=1, utom=None):
    """Första träffen på monster efter något av orden, på samma rad eller raden efter.
    utom: regex för rader som ska hoppas över (t.ex. 'exkl. moms' när momsbeloppet söks)."""
    rader = text.splitlines()
    for ordet in ord_lista:
        for i, rad in enumerate(rader):
            m = re.search(rf"\b{ordet}\b", rad, re.I)
            if not m or (utom and re.search(utom, rad, re.I)):
                continue
            for kandidat in [rad[m.end():]] + rader[i + 1:i + 1 + rader_framat]:
                for t in re.finditer(monster, kandidat):
                    v = t.group(1)
                    # hoppa över procentsatser ("Moms 25 %")
                    efter = kandidat[t.end():t.end() + 2]
                    if "%" in efter:
                        continue
                    return v
    return None


def _belopp_efter(text, ord_lista, utom=None):
    v = _efter(text, ord_lista, BELOPP, utom=utom)
    return tal(v) if v else None


def tolka(data, leverantorer=None, eget_orgnr=""):
    """Returnerar dict med förslag: fakturadatum, forfallodatum, fakturanummer, ocr, belopp, moms, momssats,
    leverantor (objekt), bankgiro, orgnr samt 'hittat' (lista med vad som lästes) och 'text'."""
    text = text_ur_pdf(data)
    if len(text.strip()) < 20:
        raise TolkningsFel("PDF:en innehåller ingen läsbar text (troligen ett inskannat kvitto). Fyll i uppgifterna själv.")
    res = {"text": text[:3000], "hittat": []}

    if v := _efter(text, [r"fakturadatum", r"invoice date", r"köpdatum", r"kvittodatum", r"datum", r"date"], DATUM, 2):
        res["fakturadatum"] = datum(v)
    if not res.get("fakturadatum"):
        alla = [datum(m.group(1)) for m in re.finditer(DATUM, text)]
        alla = [d for d in alla if d]
        if alla:
            res["fakturadatum"] = min(alla)
    if v := _efter(text, [r"förfallodatum", r"förfallodag", r"betala senast", r"att betala senast", r"due date"],
                   DATUM, 2):
        res["forfallodatum"] = datum(v)
    if v := _efter(text, [r"fakturanummer", r"fakturanr\.?", r"faktura nr\.?", r"invoice number", r"invoice no\.?",
                          r"kvittonummer", r"kvitto nr"], r"([A-Z0-9][A-Z0-9\-/]{1,29})", 1):
        res["fakturanummer"] = v
    if v := _efter(text, [r"ocr-nummer", r"ocr-nr", r"ocr", r"betalningsreferens"], r"(\d[\d ]{1,29}\d)", 1):
        siffror = v.replace(" ", "")
        if _ocr_giltig(siffror):
            res["ocr"] = siffror
    if m := re.search(r"\b(?:bankgiro|bg)[:\s.nr]*?(\d{3,4}-\d{4})\b", text, re.I):
        res["bankgiro"] = m.group(1)
    if m := re.search(r"\b(?:plusgiro|pg)[:\s.nr]*?(\d{1,7}-\d)\b", text, re.I):
        res["plusgiro"] = m.group(1)
    orgnr = [o for o in re.findall(r"\b(\d{6}-\d{4})\b", text)
             if o.replace("-", "") != (eget_orgnr or "").replace("-", "")]
    if orgnr:
        res["orgnr"] = orgnr[0]

    belopp = _belopp_efter(text, TOTAL_ORD, utom=r"exkl|exklusive|netto|varav|moms")
    moms = _belopp_efter(text, MOMS_ORD, utom=r"exkl|exklusive|utan moms|underlag|momspliktig|reg\.?\s*nr|registrering")
    if belopp is not None:
        res["belopp"] = abs(belopp)
    if moms is not None and belopp is not None and abs(moms) < abs(belopp):
        res["moms"] = abs(moms)
    if res.get("belopp") and res.get("moms"):
        netto = res["belopp"] - res["moms"]
        if netto > 0:
            sats = res["moms"] / netto * 100
            for s in (25, 12, 6):
                if abs(sats - s) < Decimal("0.6"):
                    res["momssats"] = s
    elif res.get("belopp") and re.search(r"\b(?:moms|vat)\b[^\n]*\b0(?:[.,]00)?\b", text, re.I):
        res["moms"], res["momssats"] = Decimal(0), 0

    if leverantorer is not None:
        res["leverantor"] = hitta_leverantor(text, res, leverantorer)
    res["hittat"] = [k for k in ("leverantor", "fakturadatum", "forfallodatum", "fakturanummer", "ocr", "belopp",
                                 "moms", "bankgiro", "orgnr") if res.get(k) not in (None, "")]
    return res


def _siffror(s):
    return "".join(c for c in s or "" if c.isdigit())


def hitta_leverantor(text, res, leverantorer):
    for lev in leverantorer:
        if res.get("orgnr") and lev.orgnr and _siffror(lev.orgnr)[-10:] == _siffror(res["orgnr"]):
            return lev
    for lev in leverantorer:
        if res.get("bankgiro") and lev.bankgiro and _siffror(lev.bankgiro) == _siffror(res["bankgiro"]):
            return lev
        if res.get("plusgiro") and lev.plusgiro and _siffror(lev.plusgiro) == _siffror(res["plusgiro"]):
            return lev
    lagre = text.lower()
    kandidater = [lev for lev in leverantorer if len(lev.namn) >= 4 and lev.namn.lower() in lagre]
    return max(kandidater, key=lambda lev: len(lev.namn)) if kandidater else None
