"""Affärslogik för tid och fakturering, gemensam för webbsidorna och Claude-kopplingen."""

from collections import OrderedDict
from decimal import Decimal

from django.db import transaction

from register.models import Foretag, Konto

from .models import Faktura, Fakturarad, Tidpost, avrunda

ENHET_TEXT = {"HUR": "tim", "PCE": "st", "DAY": "dag", "MON": "mån"}
MANADER = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september",
           "oktober", "november", "december"]


def periodtext(fran, till):
    if (fran.year, fran.month) == (till.year, till.month):
        return f"{MANADER[fran.month - 1]} {fran.year}".capitalize()
    return f"{fran:%Y-%m-%d} – {till:%Y-%m-%d}"


def rad_fran_uppdrag(faktura, uppdrag, timmar, beskrivning, ordning):
    a = uppdrag.artikel
    konto = a.intaktskonto if a else Konto.objects.filter(pk=3041).first()
    return Fakturarad.objects.create(
        faktura=faktura, artikel=a, beskrivning=beskrivning, antal=timmar,
        enhet=ENHET_TEXT.get(a.enhet, "tim") if a else "tim", a_pris=uppdrag.pris,
        momssats=a.momssats if a else Decimal(25), konto=konto,
        kostnadsstalle=uppdrag.kostnadsstalle, ordning=ordning,
    )


def ofakturerad_tid(kund, till_och_med):
    """Fakturerbar tid för kunden som ännu inte fakturerats, till och med datumet."""
    return list(Tidpost.objects.filter(
        uppdrag__kund=kund, fakturerbar=True, fakturarad__isnull=True,
        datum__lte=till_och_med).select_related("uppdrag__artikel").order_by("datum"))


def sammanstall(poster):
    """Timmar och belopp per uppdrag, och summa exkl. moms."""
    per_uppdrag = OrderedDict()
    for p in poster:
        s = per_uppdrag.setdefault(p.uppdrag, {"timmar": Decimal(0), "belopp": Decimal(0)})
        s["timmar"] += p.timmar
        s["belopp"] += avrunda(p.timmar * p.uppdrag.pris)
    return per_uppdrag, sum((s["belopp"] for s in per_uppdrag.values()), Decimal(0))


@transaction.atomic
def skapa_fakturautkast(kund, till_och_med, fakturadatum, en_rad_per_post=False):
    """Skapar ett fakturautkast av kundens ofakturerade tid och markerar tiden som fakturerad.

    Utkastet får varken nummer eller bokföring; det sker när användaren slutför det. Returnerar fakturan
    eller None om det inte finns någon tid.
    """
    poster = ofakturerad_tid(kund, till_och_med)
    if not poster:
        return None
    forsta_uppdrag = poster[0].uppdrag
    f = Faktura.objects.create(
        kund=kund, fakturadatum=fakturadatum,
        er_referens=forsta_uppdrag.kundreferens or kund.kontaktperson,
        var_referens=Foretag.hamta().var_referens, ordernummer=forsta_uppdrag.ordernummer,
        period=periodtext(poster[0].datum, poster[-1].datum),
    )
    grupper = OrderedDict()
    for p in poster:
        grupper.setdefault(p.uppdrag, []).append(p)
    ordning = 0
    for uppdrag, pp in grupper.items():
        if en_rad_per_post:
            for p in pp:
                ordning += 1
                text = f"{p.datum:%Y-%m-%d} {uppdrag.namn}" + (f": {p.beskrivning}" if p.beskrivning else "")
                rad = rad_fran_uppdrag(f, uppdrag, p.timmar, text, ordning)
                p.fakturarad = rad
                p.save(update_fields=["fakturarad"])
        else:
            ordning += 1
            text = f"{uppdrag.artikel.namn if uppdrag.artikel else uppdrag.namn}, {periodtext(pp[0].datum, pp[-1].datum)}"
            if uppdrag.artikel and uppdrag.artikel.namn != uppdrag.namn:
                text = f"{uppdrag.namn} – {text}"
            rad = rad_fran_uppdrag(f, uppdrag, sum(p.timmar for p in pp), text, ordning)
            Tidpost.objects.filter(id__in=[p.id for p in pp]).update(fakturarad=rad)
    return f
