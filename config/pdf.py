"""Enkel PDF-generering (fpdf2) för rapporter, arkiv och fakturor.

Använder PDF-standardtypsnittet Helvetica, som täcker svenska tecken (latin-1). Tecken utanför
latin-1, som tankstreck och citattecken från Word, byts mot närmaste motsvarighet.
"""

from decimal import Decimal

from fpdf import FPDF

ERSATT = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
          "…": "...", " ": " ", "−": "-", "€": "EUR", " ": " ", " ": " "}


def text(v):
    s = "" if v is None else str(v)
    for a, b in ERSATT.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def kr(v, decimaler=2):
    """1234567.5 -> '1 234 567,50'"""
    if v is None or v == "":
        return ""
    v = Decimal(v)
    s = f"{abs(v):,.{decimaler}f}".replace(",", " ").replace(".", ",")
    return ("-" if v < 0 else "") + s


class Rapport(FPDF):
    """A4-rapport med rubrik, sidhuvud och sidnumrering."""

    def __init__(self, titel, undertitel="", foretag="", liggande=False):
        super().__init__(orientation="L" if liggande else "P", unit="mm", format="A4")
        self.titel, self.undertitel, self.foretag = text(titel), text(undertitel), text(foretag)
        self.set_auto_page_break(True, margin=15)
        self.set_margins(15, 15, 15)
        self.alias_nb_pages()
        self.set_title(self.titel)
        self.set_creator("Bitero ERP")
        self.add_page()

    def header(self):
        self.set_font("Helvetica", "B", 13)
        self.cell(0, 7, self.titel, new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(90)
        rad = " - ".join(x for x in (self.foretag, self.undertitel) if x)
        if rad:
            self.cell(0, 5, rad, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0)
        self.ln(3)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(120)
        self.cell(0, 5, f"Sida {self.page_no()} av {{nb}}", align="R")
        self.set_text_color(0)

    def rubrik(self, t, storlek=11):
        if self.get_y() > self.h - 40:
            self.add_page()
        self.ln(2)
        self.set_font("Helvetica", "B", storlek)
        self.cell(0, 6, text(t), new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)

    def stycke(self, t, storlek=9):
        self.set_font("Helvetica", "", storlek)
        self.multi_cell(0, 4.5, text(t), new_x="LMARGIN", new_y="NEXT")

    def tabell(self, kolumner, rader, summa=None, storlek=8):
        """kolumner: [(rubrik, bredd_mm, 'L'|'R')]. rader: listor med värden (redan formaterade)."""
        hojd = storlek * 0.5

        def rubrikrad():
            self.set_font("Helvetica", "B", storlek)
            self.set_fill_color(235)
            for namn, bredd, just in kolumner:
                self.cell(bredd, hojd + 1, text(namn), border="B", align=just, fill=True)
            self.ln()
            self.set_font("Helvetica", "", storlek)

        rubrikrad()
        for rad in rader:
            fet = isinstance(rad, dict) and rad.get("fet")
            varden = rad["varden"] if isinstance(rad, dict) else rad
            if self.get_y() + hojd > self.h - 15:
                self.add_page()
                rubrikrad()
            if fet:
                self.set_font("Helvetica", "B", storlek)
            for (namn, bredd, just), v in zip(kolumner, varden):
                t = text(v)
                while t and self.get_string_width(t) > bredd - 1:
                    t = t[:-1]
                self.cell(bredd, hojd, t, align=just)
            self.ln()
            if fet:
                self.set_font("Helvetica", "", storlek)
        if summa:
            self.set_font("Helvetica", "B", storlek)
            for (namn, bredd, just), v in zip(kolumner, summa):
                self.cell(bredd, hojd + 1, text(v), border="T", align=just)
            self.ln()
            self.set_font("Helvetica", "", storlek)

    def bytes(self):
        return bytes(self.output())
