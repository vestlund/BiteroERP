"""Uppdragsuppföljning: beläggning, intäkt per kund och uppdrag som håller på att ta slut."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from .models import Faktura, Tidpost, Uppdrag

NOLL = Decimal(0)
TIMMAR_PER_DAG = Decimal(8)
VARNA_DAGAR = 60


def belaggning(ar, idag):
    """Fakturerbara timmar per månad mot möjliga timmar (arbetsdagar × 8)."""
    from budget.tjanster import arbetsdagar
    dagar = arbetsdagar(ar)
    timmar = [NOLL] * 12
    for m, s in (Tidpost.objects.filter(datum__year=ar, fakturerbar=True).values_list("datum__month")
                 .annotate(s=Sum("timmar"))):
        timmar[m - 1] = Decimal(str(s or 0))
    res = []
    for i in range(12):
        mojliga = dagar[i] * TIMMAR_PER_DAG
        if ar == idag.year and i + 1 > idag.month:
            break
        res.append({"manad": i + 1, "timmar": timmar[i], "mojliga": mojliga,
                    "procent": (timmar[i] / mojliga * 100).quantize(Decimal(1)) if mojliga else NOLL})
    tot_t = sum((r["timmar"] for r in res), NOLL)
    tot_m = sum((r["mojliga"] for r in res), NOLL)
    return {"manader": res, "timmar": tot_t, "mojliga": tot_m,
            "procent": (tot_t / tot_m * 100).quantize(Decimal(1)) if tot_m else NOLL}


def per_kund(ar):
    """Fakturerat (exkl. moms) och timmar per kund i år och föregående år."""
    rader = defaultdict(lambda: {"i_ar": NOLL, "fg_ar": NOLL, "timmar": NOLL})
    for f in Faktura.objects.filter(status__in=[Faktura.SKICKAD, Faktura.BETALD],
                                    fakturadatum__year__in=[ar, ar - 1]).select_related("kund").prefetch_related("rader"):
        netto = f.summering()["netto"]
        rader[f.kund]["i_ar" if f.fakturadatum.year == ar else "fg_ar"] += netto
    from register.models import Kund
    for kund_id, s in (Tidpost.objects.filter(datum__year=ar, fakturerbar=True).values_list("uppdrag__kund")
                       .annotate(s=Sum("timmar"))):
        rader[Kund.objects.get(pk=kund_id)]["timmar"] += Decimal(str(s or 0))
    lista = []
    total = sum((v["i_ar"] for v in rader.values()), NOLL)
    for kund, v in rader.items():
        lista.append({"kund": kund, **v, "andel": (v["i_ar"] / total * 100).quantize(Decimal(1)) if total else NOLL,
                      "snittpris": (v["i_ar"] / v["timmar"]).quantize(Decimal(1)) if v["timmar"] else None})
    return sorted(lista, key=lambda r: -r["i_ar"]), total


def uppdrag_status(idag):
    """Aktiva uppdrag med slutdatum, återstående dagar och varning om inget tar vid."""
    aktiva = list(Uppdrag.objects.filter(aktiv=True).select_related("kund", "artikel"))
    sedan = idag - dt.timedelta(days=30)
    tim30 = dict(Tidpost.objects.filter(datum__gte=sedan).values_list("uppdrag").annotate(s=Sum("timmar")))
    res = []
    for u in aktiva:
        dagar_kvar = (u.slutdatum - idag).days if u.slutdatum else None
        fortsatter = [a for a in aktiva if a.pk != u.pk and (a.slutdatum is None or (u.slutdatum and a.slutdatum >
                                                                                     u.slutdatum))
                      and (a.startdatum is None or not u.slutdatum or a.startdatum <= u.slutdatum + dt.timedelta(days=31))]
        varning = None
        if dagar_kvar is not None and dagar_kvar < 0:
            varning = "Slutdatumet har passerat – förläng eller markera uppdraget som avslutat."
        elif dagar_kvar is not None and dagar_kvar <= VARNA_DAGAR and not fortsatter:
            varning = f"Tar slut om {dagar_kvar} dagar och inget annat uppdrag tar vid."
        anvant = u.timmar_totalt()
        res.append({"uppdrag": u, "dagar_kvar": dagar_kvar, "varning": varning, "timmar_30": Decimal(str(tim30.get(u.pk) or 0)),
                    "timmar": anvant, "budget_procent": (anvant / u.budget_timmar * 100).quantize(Decimal(1))
                    if u.budget_timmar else None})
    return sorted(res, key=lambda r: (r["dagar_kvar"] is None, r["dagar_kvar"] or 0))


def varningar(idag):
    return [r for r in uppdrag_status(idag) if r["varning"]]
