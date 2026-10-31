"""Data till startsidans översikt."""

import datetime as dt
from decimal import Decimal

from django.shortcuts import render
from django.utils import timezone

from bokforing import tjanster as bok
from bokforing.models import Bankpost, Leverantorsfaktura
from register.models import Foretag

from .models import Faktura, Tidpost, Uppdrag, avrunda

MANADER = ["Jan", "Feb", "Mar", "Apr", "Maj", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]


def _f(v):
    return float(v or 0)


def oversikt(request):
    idag = timezone.localdate()
    ar = idag.year
    foretag = Foretag.hamta()

    # --- kund- och leverantörsfakturor per månad
    kund = {"betald": [0.0] * 12, "obetald": [0.0] * 12}
    kund_obetalda = []
    for f in Faktura.objects.filter(status__in=[Faktura.SKICKAD, Faktura.BETALD]).select_related(
            "kund").prefetch_related("rader"):
        belopp = f.summering()["att_betala"]
        if f.fakturadatum.year == ar:
            kund["betald" if f.status == Faktura.BETALD else "obetald"][f.fakturadatum.month - 1] += _f(belopp)
        if f.status == Faktura.SKICKAD:
            kund_obetalda.append((f, belopp))
    lev = {"betald": [0.0] * 12, "obetald": [0.0] * 12}
    lev_obetalda = []
    for lf in Leverantorsfaktura.objects.filter(typ=Leverantorsfaktura.FAKTURA):
        if lf.fakturadatum.year == ar:
            lev["betald" if lf.betald else "obetald"][lf.fakturadatum.month - 1] += _f(lf.belopp)
        if not lf.betald:
            lev_obetalda.append(lf)

    def delning(lista, datum, belopp):
        forf = [x for x in lista if datum(x) and datum(x) < idag]
        ej = [x for x in lista if x not in forf]
        return {"forf_antal": len(forf), "forf": sum((belopp(x) for x in forf), Decimal(0)),
                "ej_antal": len(ej), "ej": sum((belopp(x) for x in ej), Decimal(0))}

    kund_status = delning(kund_obetalda, lambda x: x[0].forfallodatum, lambda x: x[1])
    lev_status = delning(lev_obetalda, lambda x: x.forfallodatum, lambda x: x.belopp)

    # --- saldon och resultat
    s_ar = bok.saldon(dt.date(ar, 1, 1), idag)
    ub = lambda k: s_ar.get(k, {}).get("ub", Decimal(0))  # noqa: E731
    intakter = -sum((v["period"] for k, v in s_ar.items() if 3000 <= k < 4000), Decimal(0))
    kostnader = sum((v["period"] for k, v in s_ar.items() if 4000 <= k < 9000), Decimal(0))
    resultat = intakter - kostnader
    senaste_bank = Bankpost.objects.filter(konto_id=1930, saldo__isnull=False).order_by("-datum", "-id").first()

    # --- likviditetsprognos per vecka (12 veckor), se planering/likviditet.py
    from planering import likviditet
    lp = likviditet.prognos(idag, veckor=12)
    prognos = [{"etikett": f"v{v['vecka']}", "varde": _f(v["saldo"]),
                "detalj": "".join(f"<div>{k}: {_f(b):,.0f} kr</div>".replace(",", " ")
                                  for k, b in v["kategorier"].items() if b)} for v in lp["veckor"]]

    # --- att göra
    attgora = []
    if n := Bankpost.objects.filter(verifikation__isnull=True, ignorerad=False).count():
        attgora.append(("bla", "⇄", f"{n} bankhändelser att stämma av", "Matcha mot fakturor eller bokför", "bank"))
    if kund_status["forf_antal"]:
        attgora.append(("rod", "!", f"{kund_status['forf_antal']} förfallna kundfakturor",
                        f"{_f(kund_status['forf']):,.0f} kr".replace(",", " "), "faktura_lista"))
    if lev_status["forf_antal"]:
        attgora.append(("rod", "!", f"{lev_status['forf_antal']} förfallna leverantörsfakturor",
                        f"{_f(lev_status['forf']):,.0f} kr".replace(",", " "), "levfakturor"))
    if n := Faktura.objects.filter(status=Faktura.UTKAST).count():
        attgora.append(("gul", "✎", f"{n} fakturautkast", "Väntar på att slutföras", "faktura_lista"))
    ofakt = list(Tidpost.objects.filter(fakturerbar=True, fakturarad__isnull=True).select_related("uppdrag__artikel"))
    ofakt_belopp = sum((t.timmar * t.uppdrag.pris for t in ofakt), Decimal(0))
    if ofakt:
        attgora.append(("gron", "⏱", f"{avrunda(sum(t.timmar for t in ofakt))} ofakturerade timmar",
                        f"Cirka {_f(ofakt_belopp):,.0f} kr att fakturera".replace(",", " "), "fakturera_tid"))
    try:
        from lon.models import Lonekorning
        if n := Lonekorning.objects.filter(status=Lonekorning.UTKAST).count():
            attgora.append(("gul", "₪", f"{n} lönekörning ej bokförd", "Kontrollera och bokför", "lonekorningar"))
    except Exception:
        pass
    if not foretag.orgnr:
        attgora.append(("gul", "⚙", "Fyll i företagsuppgifter", "Org.nr, momsnummer och bankgiro", "foretag"))

    timmar_manad = avrunda(sum((t.timmar for t in Tidpost.objects.filter(datum__gte=idag.replace(day=1))),
                               Decimal(0)))
    uppdrag = list(Uppdrag.objects.filter(aktiv=True).select_related("kund", "artikel"))
    for u in uppdrag:
        u.ofakt = u.timmar_ofakturerade()
        u.totalt = u.timmar_totalt()

    budgetinfo = None
    try:
        from budget.models import Budget
        from budget.tjanster import uppfoljning
        bud = Budget.objects.filter(ar=ar, huvudbudget=True).first()
        if bud:
            uf = uppfoljning(bud, idag)
            budgetinfo = {"budget": bud, "s": uf["summa"],
                          "ack": {"budget": [_f(v) for v in uf["ack"]["budget"]],
                                  "utfall": [_f(v) for v in uf["ack"]["utfall"]],
                                  "prognos": [_f(v) for v in uf["ack"]["prognos"]]}}
    except Exception:
        budgetinfo = None

    return render(request, "verksamhet/oversikt.html", {
        "budgetinfo": budgetinfo,
        "ar": ar, "idag": idag,
        "fakturadiagram": {"kategorier": MANADER, "kund": kund, "lev": lev},
        "kund_status": kund_status, "lev_status": lev_status,
        "intakter": intakter, "kostnader": kostnader, "resultat": resultat,
        "bank_bokfort": ub(1930), "skattekonto": ub(1630), "senaste_bank": senaste_bank,
        "prognos": prognos, "attgora": attgora, "timmar_manad": timmar_manad, "ofakt_belopp": ofakt_belopp,
        "uppdrag": uppdrag,
        "resultatdiagram": {"intakter": _f(intakter), "kostnader": _f(kostnader), "resultat": _f(resultat)},
    })
