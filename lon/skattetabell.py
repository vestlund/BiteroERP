"""Skattetabeller från Skatteverket (allmänna tabeller, månadslön).

Import: Skatteverkets öppna data som CSV (kolumner: år, antal dgr, tabellnr,
inkomst fr.o.m., inkomst t.o.m., kolumn 1 … kolumn 6). Raderna med "30B"
(belopp) och "30%" (procent) används; övriga perioder hoppas över.
"""

import csv
import io
from decimal import ROUND_HALF_UP, Decimal

from .models import Skattetabellrad


class TabellFel(Exception):
    pass


def _norm(t):
    return t.strip().strip('"').lower().replace(".", "").replace(" ", "").replace("ö", "o").replace("å", "a")


def importera(data: bytes):
    for kod in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(kod)
            break
        except UnicodeDecodeError:
            continue
    forsta = text.splitlines()[0] if text else ""
    sep = ";" if forsta.count(";") > forsta.count(",") else ","
    rader = list(csv.reader(io.StringIO(text), delimiter=sep))
    if not rader:
        raise TabellFel("Filen är tom.")
    rubrik = [_norm(r) for r in rader[0]]

    def idx(*namn):
        for n in namn:
            if n in rubrik:
                return rubrik.index(n)
        raise TabellFel(f"Hittar inte kolumnen '{namn[0]}' i filen. Rubriker: {', '.join(rader[0])}")

    i_ar, i_typ, i_tab = idx("ar"), idx("antaldgr", "antaldagar"), idx("tabellnr", "tabell")
    i_fran, i_till = idx("inkomstfrom", "inkomstfran"), idx("inkomsttom", "inkomsttill")
    i_kol = [idx(f"kolumn{n}") for n in range(1, 7)]

    nya, ar_set = [], set()
    for r in rader[1:]:
        if len(r) <= max(i_kol):
            continue
        typ = r[i_typ].strip().upper()
        if typ not in ("30B", "30%"):
            continue
        tal = lambda v: int(Decimal((v or "0").replace(" ", "").replace(",", ".") or "0"))  # noqa: E731
        ar = int(r[i_ar])
        ar_set.add(ar)
        nya.append(Skattetabellrad(
            ar=ar, tabell=int(r[i_tab]), procent=typ.endswith("%"), inkomst_fran=tal(r[i_fran]),
            inkomst_till=tal(r[i_till]) if r[i_till].strip() else 10 ** 9,
            **{f"kolumn{n + 1}": tal(r[i]) for n, i in enumerate(i_kol)}))
    if not nya:
        raise TabellFel("Inga rader för månadslön (30B/30%) hittades i filen.")
    Skattetabellrad.objects.filter(ar__in=ar_set).delete()
    Skattetabellrad.objects.bulk_create(nya, batch_size=2000)
    return sorted(ar_set), len(nya)


def sla_upp(ar, tabell, kolumn, underlag):
    if not tabell:
        return None
    belopp = int(Decimal(underlag).to_integral_value(rounding=ROUND_HALF_UP))
    rad = Skattetabellrad.objects.filter(ar=ar, tabell=tabell, inkomst_fran__lte=belopp,
                                         inkomst_till__gte=belopp).first()
    if not rad:
        return None
    varde = getattr(rad, f"kolumn{kolumn}")
    if rad.procent:
        return (Decimal(underlag) * varde / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return Decimal(varde)


def har_tabell(ar):
    return Skattetabellrad.objects.filter(ar=ar).exists()
