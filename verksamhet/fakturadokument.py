"""Fakturor och påminnelser som PDF och som e-postutkast.

- PDF skapas med config.pdf (fpdf2).
- OCR-nummer: fakturanumret + längdsiffra + kontrollsiffra (Luhn), samma format som Visma använder
  (faktura 178 -> 17855), så att bankgirots OCR-kontroll fungerar likadant för gamla och nya fakturor.
- E-post: en .eml-fil märkt som osänt utkast (X-Unsent: 1). Den öppnas i Outlook som ett nytt mejl med
  fakturan bifogad, så att du kan läsa igenom och skicka från ditt vanliga konto.
- Dröjsmålsränta enligt räntelagen: referensräntan + 8 procentenheter, per kalenderhalvår.
"""

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from config.pdf import Rapport, kr, text

# Riksbankens referensränta (procent) per kalenderhalvår. Källa: riksbank.se. Uppdateras 1 jan och 1 jul.
REFERENSRANTA = {(2024, 1): Decimal("4.00"), (2024, 2): Decimal("4.00"), (2025, 1): Decimal("3.00"),
                 (2025, 2): Decimal("2.00"), (2026, 1): Decimal("2.00"), (2026, 2): Decimal("2.00")}
DROJSMAL_TILLAGG = Decimal("8")


def luhn_siffra(s):
    summa = 0
    for i, c in enumerate(reversed(s)):
        n = int(c) * (2 if i % 2 == 0 else 1)
        summa += n - 9 if n > 9 else n
    return str((10 - summa % 10) % 10)


def ocr_for(nummer):
    bas = str(nummer)
    langd = str((len(bas) + 2) % 10)
    return bas + langd + luhn_siffra(bas + langd)


def referensranta(d):
    nyckel = (d.year, 1 if d.month <= 6 else 2)
    if nyckel in REFERENSRANTA:
        return REFERENSRANTA[nyckel]
    return REFERENSRANTA[max(k for k in REFERENSRANTA if k <= nyckel)] if any(k <= nyckel for k in REFERENSRANTA) \
        else REFERENSRANTA[min(REFERENSRANTA)]


def drojsmalsranta(belopp, forfallodag, till):
    """Upplupen dröjsmålsränta från dagen efter förfallodagen till och med 'till'."""
    if not forfallodag or till <= forfallodag:
        return Decimal(0), 0
    ranta, d = Decimal(0), forfallodag + dt.timedelta(days=1)
    while d <= till:
        halvar_slut = dt.date(d.year, 6, 30) if d.month <= 6 else dt.date(d.year, 12, 31)
        slut = min(halvar_slut, till)
        dagar = (slut - d).days + 1
        sats = (referensranta(d) + DROJSMAL_TILLAGG) / 100
        ranta += Decimal(belopp) * sats * dagar / 365
        d = slut + dt.timedelta(days=1)
    return ranta.quantize(Decimal("1"), rounding=ROUND_HALF_UP), (till - forfallodag).days


# ------------------------------------------------------------------ PDF

class Fakturadokument(Rapport):
    def __init__(self, foretag, rubrik):
        self.f_foretag = foretag
        super().__init__(rubrik, "", "")

    def header(self):
        pass

    def footer(self):
        f = self.f_foretag
        self.set_y(-26)
        self.set_draw_color(150)
        self.line(15, self.get_y(), 195, self.get_y())
        self.ln(2)
        kol = [
            (f.namn, f"{f.adress}\n{f.postnummer} {f.ort}"),
            ("Kontakt", "\n".join(x for x in (f.telefon, f.epost, f.webb) if x)),
            ("Betalning", "\n".join(x for x in (f"Bankgiro {f.bankgiro}" if f.bankgiro else "",
                                                f"Plusgiro {f.plusgiro}" if f.plusgiro else "",
                                                f"IBAN {f.iban}" if f.iban else "", f"BIC {f.bic}" if f.bic else "")
                                    if x)),
            (f"Org.nr {f.orgnr}", f"Momsreg.nr {f.momsnummer}" + ("\nGodkänd för F-skatt" if f.f_skatt else "")),
        ]
        y = self.get_y()
        for i, (rubrik, innehall) in enumerate(kol):
            self.set_xy(15 + i * 45, y)
            self.set_font("Helvetica", "B", 7.5)
            self.cell(45, 3.5, text(rubrik), new_x="LMARGIN", new_y="NEXT")
            self.set_x(15 + i * 45)
            self.set_font("Helvetica", "", 7.5)
            self.multi_cell(44, 3.3, text(innehall))


def _huvud(pdf, foretag, titel, nummer_text):
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(110, 8, text(foretag.namn))
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 8, text(titel), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(110, 5, "")
    pdf.cell(0, 5, text(nummer_text), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)


def _fakta_och_mottagare(pdf, fakta, kund):
    y = pdf.get_y()
    pdf.set_font("Helvetica", "", 9)
    for etikett, varde in fakta:
        pdf.set_text_color(90)
        pdf.cell(32, 4.8, text(etikett))
        pdf.set_text_color(0)
        pdf.cell(60, 4.8, text(varde), new_x="LMARGIN", new_y="NEXT")
    slut_vanster = pdf.get_y()
    pdf.set_xy(115, y + 2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 5.5, text(kund.namn), new_x="LEFT", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10.5)
    for rad in (kund.adress1, kund.adress2, f"{kund.postnummer} {kund.ort}".strip(),
                kund.landkod if kund.landkod != "SE" else ""):
        if rad:
            pdf.cell(0, 5, text(rad), new_x="LEFT", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8.5)
    if kund.orgnr:
        pdf.cell(0, 4.5, text(f"Org.nr {kund.orgnr}"), new_x="LEFT", new_y="NEXT")
    if kund.momsnummer:
        pdf.cell(0, 4.5, text(f"VAT {kund.momsnummer}"), new_x="LEFT", new_y="NEXT")
    pdf.set_y(max(slut_vanster, pdf.get_y()) + 8)


def _betalningsrad(pdf, foretag, ocr, nummer):
    if not (foretag.bankgiro or foretag.plusgiro):
        return
    till = " och ".join(x for x in (f"bankgiro {foretag.bankgiro}" if foretag.bankgiro else "",
                                    f"plusgiro {foretag.plusgiro}" if foretag.plusgiro else "") if x)
    ref = f"OCR {ocr}" if ocr else f"fakturanummer {nummer}"
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 10)
    pdf.multi_cell(0, 5, text(f"Betala till {till}. Ange {ref}."))


def faktura_pdf(f, foretag):
    s = f.summering()
    pdf = Fakturadokument(foretag, f"Faktura {f.nummer or 'utkast'}")
    _huvud(pdf, foretag, "FAKTURA", f"Nr {f.nummer}" if f.nummer else "UTKAST - ej giltig faktura")
    fakta = [("Fakturadatum", f.fakturadatum), ("Förfallodatum", f.forfallodatum or "-"),
             ("Betalningsvillkor", f.kund.betalningsvillkor or "30 dagar netto"), ("Kundnummer", f.kund.nummer)]
    if f.ocr:
        fakta.append(("OCR", f.ocr))
    for etikett, varde in (("Er referens", f.er_referens), ("Vår referens", f.var_referens),
                           ("Ordernummer", f.ordernummer), ("Period", f.period)):
        if varde:
            fakta.append((etikett, varde))
    fakta.append(("Dröjsmålsränta", foretag.drojsmalsranta))
    _fakta_och_mottagare(pdf, fakta, f.kund)
    kol = [("Beskrivning", 82, "L"), ("Antal", 18, "R"), ("Enhet", 14, "L"), ("À-pris", 24, "R"),
           ("Moms", 14, "R"), ("Belopp", 28, "R")]
    rader = [[r.beskrivning, kr(r.antal).rstrip("0").rstrip(","), r.enhet, kr(r.a_pris),
              f"{kr(r.momssats, 0)} %", kr(r.belopp)] for r in f.rader.all()]
    pdf.tabell(kol, rader, storlek=9)
    pdf.ln(3)
    summor = [("Belopp exkl. moms", kr(s["netto"]))]
    summor += [(f"Moms {kr(sats, 0)} % (underlag {kr(m['underlag'])})", kr(m["moms"])) for sats, m in s["moms"].items()]
    if s["oresavrundning"]:
        summor.append(("Öresavrundning", kr(s["oresavrundning"])))
    pdf.set_font("Helvetica", "", 9.5)
    for etikett, varde in summor:
        pdf.set_x(100)
        pdf.cell(65, 5.5, text(etikett))
        pdf.cell(30, 5.5, text(varde), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(100)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(65, 8, "Att betala SEK", border="T")
    pdf.cell(30, 8, text(kr(s["att_betala"])), border="T", align="R", new_x="LMARGIN", new_y="NEXT")
    if f.meddelande and not f.fran_visma:
        pdf.ln(4)
        pdf.stycke(f.meddelande, 9.5)
    _betalningsrad(pdf, foretag, f.ocr, f.nummer)
    return pdf.bytes()


def paminnelse_pdf(f, foretag, idag, avgift=Decimal(0)):
    att_betala = f.summering()["att_betala"]
    ranta, dagar = drojsmalsranta(att_betala, f.forfallodatum, idag)
    pdf = Fakturadokument(foretag, f"Påminnelse faktura {f.nummer}")
    _huvud(pdf, foretag, "PÅMINNELSE", f"Faktura nr {f.nummer}")
    fakta = [("Datum", idag), ("Fakturadatum", f.fakturadatum), ("Förfallodatum", f.forfallodatum),
             ("Dagar efter förfall", dagar), ("Kundnummer", f.kund.nummer)]
    if f.ocr:
        fakta.append(("OCR", f.ocr))
    if f.er_referens:
        fakta.append(("Er referens", f.er_referens))
    _fakta_och_mottagare(pdf, fakta, f.kund)
    pdf.stycke(f"Enligt vår bokföring är faktura {f.nummer} inte betald. Vi ber er betala det förfallna beloppet "
               f"snarast, senast inom tio dagar. Om betalningen redan är gjord kan ni bortse från denna påminnelse.", 10)
    pdf.ln(4)
    kol = [("Avser", 120, "L"), ("Belopp", 60, "R")]
    rader = [[f"Faktura {f.nummer}, förföll {f.forfallodatum}", kr(att_betala)]]
    total = att_betala
    if avgift:
        rader.append(["Påminnelseavgift", kr(avgift)])
        total += avgift
    pdf.tabell(kol, rader, summa=["Att betala SEK", kr(total)], storlek=10)
    pdf.ln(3)
    pdf.stycke(f"Dröjsmålsränta enligt räntelagen ({foretag.drojsmalsranta}) löper från förfallodagen. "
               f"Upplupen ränta hittills: {kr(ranta, 0)} kr, som kan komma att faktureras separat.", 9)
    _betalningsrad(pdf, foretag, f.ocr, f.nummer)
    return pdf.bytes(), ranta, dagar


# ------------------------------------------------------------------ e-post

def eml(foretag, till, kopia, amne, brodtext, bilagenamn, bilaga):
    m = EmailMessage()
    m["From"] = f'"{foretag.namn}" <{foretag.epost}>' if foretag.epost else foretag.namn
    if till:
        m["To"] = till
    if kopia:
        m["Cc"] = kopia
    m["Subject"] = amne
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain="bitero-erp.local")
    m["X-Unsent"] = "1"  # Outlook öppnar filen som ett nytt utkast
    m.set_content(brodtext)
    m.add_attachment(bilaga, maintype="application", subtype="pdf", filename=bilagenamn)
    return m.as_bytes()


def mottagare(kund):
    return kund.kontakt_epost or kund.epost, kund.kopia_epost


def faktura_eml(f, foretag, pdf):
    till, kopia = mottagare(f.kund)
    s = f.summering()
    hej = f"Hej {f.er_referens.split()[0]}," if f.er_referens else "Hej,"
    ref = f"OCR {f.ocr}" if f.ocr else f"fakturanummer {f.nummer}"
    brod = (f"{hej}\n\nBifogat finns faktura {f.nummer} på {kr(s['att_betala'])} kr"
            + (f" avseende {f.period}" if f.period else "") + f", med förfallodag {f.forfallodatum}.\n"
            + (f"Betala till bankgiro {foretag.bankgiro} och ange {ref}.\n" if foretag.bankgiro else "")
            + f"\nMed vänliga hälsningar\n{foretag.var_referens or foretag.namn}\n{foretag.namn}\n"
            + (f"{foretag.telefon}\n" if foretag.telefon else ""))
    return eml(foretag, till, kopia, f"Faktura {f.nummer} från {foretag.namn}", brod, f"Faktura_{f.nummer}.pdf", pdf)


def paminnelse_eml(f, foretag, pdf):
    till, kopia = mottagare(f.kund)
    hej = f"Hej {f.er_referens.split()[0]}," if f.er_referens else "Hej,"
    brod = (f"{hej}\n\nVi har inte fått betalt för faktura {f.nummer} som förföll {f.forfallodatum}. "
            f"Påminnelsen finns bifogad. Har ni redan betalat kan ni bortse från detta mejl.\n\n"
            f"Med vänliga hälsningar\n{foretag.var_referens or foretag.namn}\n{foretag.namn}\n")
    return eml(foretag, till, kopia, f"Påminnelse: faktura {f.nummer} från {foretag.namn}", brod,
               f"Paminnelse_faktura_{f.nummer}.pdf", pdf)
