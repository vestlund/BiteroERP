"""Likviditetsprognos: saldot på företagskontot vecka för vecka framåt.

In:
- obetalda kundfakturor på förfallodagen (förfallna räknas in innevarande vecka),
- ofakturerad tid: faktureras vid månadens slut och betalas efter kundens betalningsvillkor,
- budgeterade intäkter för kommande hela månader (huvudbudgeten): faktureras vid månadens slut.

Ut:
- obetalda leverantörsfakturor på förfallodagen,
- nettolöner: senaste bokförda lönekörning upprepas varje månad på samma dag,
- skatter till skattekontot på skattedagarna: personalskatt och arbetsgivaravgifter för föregående månads
  löner, debiterad F-skatt (senaste debiteringen) och moms på momsdagen,
- budgeterade kostnader för kommande månader (utom personal och skatt), jämnt den 15:e.

Startsaldot är bankens senaste saldo från importerade kontoutdrag (plus senare bankposter), annars det
bokförda saldot på 1930.
"""

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from bokforing import tjanster as bok
from bokforing.models import Bankpost, Leverantorsfaktura, Transaktion
from register.models import Foretag
from verksamhet.models import Faktura, Tidpost

from . import frister

NOLL = Decimal(0)
MOMS = Decimal("1.25")


def _manadsslut(d):
    nasta = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nasta - dt.timedelta(days=1)


def startsaldo(idag):
    post = Bankpost.objects.filter(konto_id=1930, saldo__isnull=False).order_by("-datum", "-id").first()
    if post:
        senare = Bankpost.objects.filter(konto_id=1930, datum__gt=post.datum).aggregate(s=Sum("belopp"))["s"]
        return post.saldo + Decimal(str(senare or 0)), f"enligt banken {post.datum}"
    s = bok.saldon(dt.date(idag.year, 1, 1), idag).get(1930, {}).get("ub", NOLL)
    return s, "bokfört saldo på 1930"


def _senaste_fskatt():
    foretag = Foretag.hamta()
    if foretag.fskatt_manad:
        return foretag.fskatt_manad
    t = Transaktion.objects.filter(konto_id=2518, belopp__gt=0).order_by("-verifikation__datum").first()
    return t.belopp if t else NOLL


def _manad(ar, m, konton):
    qs = Transaktion.objects.filter(verifikation__datum__year=ar, verifikation__datum__month=m,
                                    konto__nummer__range=konton)
    return qs.aggregate(d=Sum("belopp"))["d"]


def lon_bokford(ar, m):
    return bool(_manad(ar, m, (7000, 7299)))


def lon_skatter(ar, m):
    """Personalskatt och arbetsgivaravgifter för lön utbetald en månad, enligt bokföringen (None om ingen lön)."""
    if not lon_bokford(ar, m):
        return None
    skatt = -Decimal(str(_manad(ar, m, (2710, 2710)) or 0))
    avgift = -Decimal(str(_manad(ar, m, (2730, 2739)) or 0))
    return max(skatt, NOLL) + max(avgift, NOLL)


def lonemonster(idag):
    """Hur en vanlig lönemånad ser ut: från senaste lönekörningen här, annars från bokföringen (t.ex. Visma)."""
    from lon.models import Lonekorning
    korning = Lonekorning.objects.filter(status=Lonekorning.BOKFORD).order_by("-utbetalningsdatum").first()
    m_ar, m = idag.year, idag.month
    for _ in range(4):  # senaste månaden med bokförd lön
        if lon_bokford(m_ar, m):
            break
        m_ar, m = (m_ar - (m == 1), (m - 2) % 12 + 1)
    else:
        m_ar = None
    if korning and (m_ar is None or korning.utbetalningsdatum >= dt.date(m_ar, m, 1)):
        s = korning.summor()
        return {"netto": s["netto"] + s["skattefritt"], "skatter": s["skatt"] + s["avgift"],
                "dag": korning.utbetalningsdatum.day, "kalla": f"som lönekörningen {korning.period}"}
    if m_ar is None:
        return None
    brutto = Decimal(str(_manad(m_ar, m, (7000, 7299)) or 0))
    skatt = -Decimal(str(_manad(m_ar, m, (2710, 2710)) or 0))
    return {"netto": (brutto - skatt).quantize(Decimal(1)), "skatter": lon_skatter(m_ar, m).quantize(Decimal(1)),
            "dag": 25, "kalla": f"uppskattat från bokförd lön {m_ar}-{m:02d}"}


def prognos(idag=None, veckor=13):
    idag = idag or timezone.localdate()
    foretag = Foretag.hamta()
    vecka0 = idag - dt.timedelta(days=idag.weekday())
    slut = vecka0 + dt.timedelta(weeks=veckor) - dt.timedelta(days=1)
    poster = []  # (datum, kategori, belopp, text)

    def lagg(datum, kategori, belopp, text=""):
        if belopp:
            poster.append((max(datum, idag), kategori, belopp, text))

    # --- kundfakturor
    for f in Faktura.objects.filter(status=Faktura.SKICKAD).select_related("kund").prefetch_related("rader"):
        lagg(f.forfallodatum or idag, "Kundfakturor", f.summering()["att_betala"], f"Faktura {f.nummer} {f.kund.namn}")

    # --- ofakturerad tid: faktureras vid månadsskiftet
    fakt_dag = _manadsslut(idag)
    per_kund = defaultdict(lambda: [NOLL, 30])
    for t in Tidpost.objects.filter(fakturerbar=True, fakturarad__isnull=True).select_related(
            "uppdrag__kund__betalningsvillkor", "uppdrag__artikel"):
        k = per_kund[t.uppdrag.kund.namn]
        k[0] += t.timmar * t.uppdrag.pris
        k[1] = t.uppdrag.kund.betalningsvillkor.dagar if t.uppdrag.kund.betalningsvillkor else 30
    moms_extra = defaultdict(lambda: NOLL)  # momsperiodens slut -> utgående moms från prognosfakturor
    for kund, (netto, dagar) in per_kund.items():
        lagg(fakt_dag + dt.timedelta(days=dagar), "Ofakturerad tid", (netto * MOMS).quantize(Decimal(1)),
             f"{kund}: faktureras {fakt_dag}")
        moms_extra[frister.momsperiod_for(fakt_dag, foretag.momsperiod)[1]] += netto * (MOMS - 1)

    # --- budget för kommande hela månader
    from budget.models import Budget, Budgetrad
    m_ar, m = idag.year, idag.month
    for _ in range(veckor // 4 + 2):
        m_ar, m = (m_ar + (m == 12), m % 12 + 1)
        manad = dt.date(m_ar, m, 1)
        if manad > slut:
            break
        bud = Budget.objects.filter(ar=m_ar, huvudbudget=True).prefetch_related("rader__konto").first()
        if not bud:
            continue
        intakt = kostnad = NOLL
        for rad in bud.rader.all():
            v = rad.varden[m - 1]
            if rad.typ == Budgetrad.TID:
                intakt += v * (rad.timpris or 0)
            elif rad.typ == Budgetrad.INTAKT:
                intakt += v
            elif not (rad.konto_id and (7000 <= rad.konto_id < 7700 or rad.konto_id >= 8000)):
                kostnad += v
        fakt = _manadsslut(manad)
        lagg(fakt + dt.timedelta(days=30), "Budgeterade intäkter", (intakt * MOMS).quantize(Decimal(1)),
             f"Budget {manad:%Y-%m}, faktureras {fakt}")
        moms_extra[frister.momsperiod_for(fakt, foretag.momsperiod)[1]] += intakt * (MOMS - 1)
        lagg(dt.date(m_ar, m, 15), "Budgeterade kostnader", -kostnad.quantize(Decimal(1)), f"Budget {manad:%Y-%m}")

    # --- leverantörsfakturor
    for lf in Leverantorsfaktura.objects.filter(betaldatum__isnull=True, typ=Leverantorsfaktura.FAKTURA).select_related(
            "leverantor"):
        lagg(lf.forfallodatum or idag, "Leverantörsfakturor", -lf.belopp, str(lf))

    # --- löner och skatter
    monster = lonemonster(idag)
    fskatt = _senaste_fskatt()
    m_ar, m = idag.year, idag.month
    for _ in range(veckor // 4 + 2):
        if monster:
            try:
                dag_lon = dt.date(m_ar, m, monster["dag"])
            except ValueError:
                dag_lon = _manadsslut(dt.date(m_ar, m, 1))
            if dag_lon >= idag and not lon_bokford(m_ar, m):
                lagg(dag_lon, "Löner", -monster["netto"], f"Nettolön {m_ar}-{m:02d} ({monster['kalla']})")
        dag_skatt = frister.skattedag(m_ar, m)
        if dag_skatt >= idag:
            f_ar, f_m = (m_ar - (m == 1), (m - 2) % 12 + 1)  # lönemånaden som deklareras
            skatt = lon_skatter(f_ar, f_m)
            delar = ["F-skatt"] if fskatt else []
            if skatt is None and monster:
                skatt = monster["skatter"]
                delar.append("skatt och avgifter för lön (uppskattat)")
            elif skatt:
                delar.append(f"skatt och avgifter för lön {f_ar}-{f_m:02d}")
            lagg(dag_skatt, "Skattekonto", -(fskatt + (skatt or NOLL)), " + ".join(delar))
        m_ar, m = (m_ar + (m == 12), m % 12 + 1)

    # --- moms: räknas på periodernas momsrapport (inte på kontosaldon, som kan innehålla gamla poster)
    nu_fran, nu_till = frister.momsperiod_for(idag, foretag.momsperiod)
    forra_fran, forra_till = frister.momsperiod_for(nu_fran - dt.timedelta(days=1), foretag.momsperiod)
    if frister.momsdag(forra_till, foretag.momsperiod) >= idag:
        lagg(frister.momsdag(forra_till, foretag.momsperiod), "Moms",
             -bok.momsrapport(forra_fran, forra_till)["rutor"]["49"], f"Moms {forra_fran} – {forra_till}")
    oppen = bok.momsrapport(nu_fran, idag)["rutor"]["49"] + moms_extra.pop(nu_till, NOLL)
    lagg(frister.momsdag(nu_till, foretag.momsperiod), "Moms", -oppen, f"Moms {nu_fran} – {nu_till} (hittills + prognos)")
    for p_slut, belopp in moms_extra.items():
        lagg(frister.momsdag(p_slut, foretag.momsperiod), "Moms", -belopp, f"Moms på prognosfakturor t.o.m. {p_slut}")

    # --- summera per vecka
    saldo, kalla = startsaldo(idag)
    start = saldo
    resultat = []
    poster.sort(key=lambda p: p[0])
    for i in range(veckor):
        v_start = vecka0 + dt.timedelta(weeks=i)
        v_slut = v_start + dt.timedelta(days=6)
        vposter = [p for p in poster if v_start <= p[0] <= v_slut]
        per_kat = defaultdict(lambda: NOLL)
        for _, kat, belopp, _ in vposter:
            per_kat[kat] += belopp
        in_ = sum((b for b in per_kat.values() if b > 0), NOLL)
        ut = sum((b for b in per_kat.values() if b < 0), NOLL)
        saldo += in_ + ut
        resultat.append({"vecka": v_start.isocalendar()[1], "fran": v_start, "till": v_slut, "in": in_, "ut": ut,
                         "saldo": saldo, "kategorier": dict(per_kat),
                         "poster": [{"datum": d, "kategori": k, "belopp": b, "text": t} for d, k, b, t in vposter]})
    lagsta = min(resultat, key=lambda r: r["saldo"]) if resultat else None
    return {"start": start, "kalla": kalla, "veckor": resultat, "lagsta": lagsta, "idag": idag,
            "fskatt": fskatt, "lon": monster, "efter_horisont": [p for p in poster if p[0] > slut]}

