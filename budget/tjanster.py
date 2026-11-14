import datetime as dt
import re
from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import ExtractMonth

from bokforing.models import Transaktion
from verksamhet.models import Faktura, Tidpost, Uppdrag
from verksamhet.tjanster import MANADER

from .models import Budgetrad

NOLL = Decimal(0)


def _d(v):
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


# ------------------------------------------------------------------ arbetsdagar

def _pask(ar):
    a, b, c = ar % 19, ar // 100, ar % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    manad = (h + l - 7 * m + 114) // 31
    dag = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(ar, manad, dag)


def helgdagar(ar):
    """Svenska helgdagar plus de dagar som i praktiken är lediga (midsommar-, jul- och nyårsafton)."""
    p = _pask(ar)
    midsommarafton = next(dt.date(ar, 6, d) for d in range(19, 26) if dt.date(ar, 6, d).weekday() == 4)
    return {
        dt.date(ar, 1, 1), dt.date(ar, 1, 6), p - dt.timedelta(days=2), p + dt.timedelta(days=1),
        dt.date(ar, 5, 1), p + dt.timedelta(days=39), dt.date(ar, 6, 6), midsommarafton,
        dt.date(ar, 12, 24), dt.date(ar, 12, 25), dt.date(ar, 12, 26), dt.date(ar, 12, 31),
    }


def arbetsdagar(ar):
    lediga = helgdagar(ar)
    res = []
    for m in range(1, 13):
        d, n = dt.date(ar, m, 1), 0
        while d.month == m:
            if d.weekday() < 5 and d not in lediga:
                n += 1
            d += dt.timedelta(days=1)
        res.append(n)
    return res


# ------------------------------------------------------------------ utfall

# En faktura daterad senast den här dagen i månaden avser normalt föregående månad
SISTA_DAG_FOR_FORRA_MANADEN = 15


def _manad_ur_period(text):
    """Månad (år, månad) ur fakturans periodtext: "Oktober 2026", "2026-10" eller "2026-10-01 – 2026-10-31"."""
    text = (text or "").strip().lower()
    if not text:
        return None
    datum = re.findall(r"(\d{4})-(\d{2})(?:-\d{2})?", text)
    if datum:
        ar, m = datum[-1]  # vid ett intervall: månaden där perioden slutar
        return (int(ar), int(m)) if 1 <= int(m) <= 12 else None
    for i, namn in enumerate(MANADER, start=1):
        if (hit := re.search(rf"\b{namn}\b\D*(\d{{4}})", text)):
            return int(hit.group(1)), i
    return None


def intaktsmanad(faktura, tidmanader=None):
    """Den månad fakturans intäkt hör till i budgetuppföljningen, som (år, månad).

    1. Fakturerad tid: den månad som har flest timmar på fakturan.
    2. Fakturans period, t.ex. "Oktober 2026".
    3. Annars fakturadatum: till och med den 15:e räknas föregående månad (arbete faktureras i efterskott).
    """
    if tidmanader:
        return max(tidmanader.items(), key=lambda kv: (kv[1], kv[0]))[0]
    if manad := _manad_ur_period(faktura.period):
        return manad
    d = faktura.fakturadatum
    if d.day <= SISTA_DAG_FOR_FORRA_MANADEN:
        forra = d.replace(day=1) - dt.timedelta(days=1)
        return forra.year, forra.month
    return d.year, d.month


def _intaktsmanader(faktura_ids):
    """{faktura_id: (år, månad)} för budgetuppföljningen."""
    tid = defaultdict(Counter)
    for fid, datum, timmar in Tidpost.objects.filter(fakturarad__faktura_id__in=faktura_ids).values_list(
            "fakturarad__faktura_id", "datum", "timmar"):
        tid[fid][(datum.year, datum.month)] += timmar
    return {f.pk: intaktsmanad(f, tid.get(f.pk)) for f in Faktura.objects.filter(pk__in=faktura_ids)}


def utfall(ar):
    """Faktiskt utfall per månad: timmar, upparbetat (tid × pris), bokförda intäkter och kostnader."""
    timmar, upparbetat = [NOLL] * 12, [NOLL] * 12
    tim_per_uppdrag = defaultdict(lambda: [NOLL] * 12)
    for t in Tidpost.objects.filter(datum__year=ar).select_related("uppdrag__artikel"):
        i = t.datum.month - 1
        tim_per_uppdrag[t.uppdrag_id][i] += t.timmar
        if t.fakturerbar:
            timmar[i] += t.timmar
            upparbetat[i] += _d(t.timmar * t.uppdrag.pris)
    intakter, kostnader = [NOLL] * 12, [NOLL] * 12
    per_konto = defaultdict(lambda: [NOLL] * 12)
    # Intäkter från kundfakturor räknas på den månad arbetet avser (se intaktsmanad), övrigt på bokföringsdatum.
    # Därför tas även fakturor med i början av nästa år (decemberarbete) och lämnas januarifakturor för
    # förra årets arbete utanför.
    qs = Transaktion.objects.filter(verifikation__datum__year=ar, konto__nummer__gte=3000, konto__nummer__lt=9000,
                                    verifikation__kundfaktura__isnull=True)
    for k, m, s in qs.annotate(m=ExtractMonth("verifikation__datum")).values_list("konto", "m").annotate(
            s=Sum("belopp")).values_list("konto", "m", "s"):
        s = _d(s)
        if k < 4000:
            intakter[m - 1] -= s
        else:
            kostnader[m - 1] += s
            per_konto[k][m - 1] += s
    fakturor = defaultdict(lambda: NOLL)
    for fid, s in Transaktion.objects.filter(
            verifikation__kundfaktura__isnull=False, konto__nummer__gte=3000, konto__nummer__lt=4000,
            verifikation__datum__gte=dt.date(ar - 1, 1, 1), verifikation__datum__lte=dt.date(ar + 1, 12, 31),
    ).values_list("verifikation__kundfaktura").annotate(s=Sum("belopp")).values_list("verifikation__kundfaktura", "s"):
        fakturor[fid] += _d(s)
    for fid, (f_ar, m) in _intaktsmanader(list(fakturor)).items():
        if f_ar == ar:
            intakter[m - 1] -= fakturor[fid]
    # Kostnadskonton (4000–8999) på kundfakturaverifikationer är ovanliga men räknas på bokföringsdatum
    for k, m, s in Transaktion.objects.filter(
            verifikation__datum__year=ar, verifikation__kundfaktura__isnull=False, konto__nummer__gte=4000,
            konto__nummer__lt=9000).annotate(m=ExtractMonth("verifikation__datum")).values_list("konto", "m").annotate(
            s=Sum("belopp")).values_list("konto", "m", "s"):
        kostnader[m - 1] += _d(s)
        per_konto[k][m - 1] += _d(s)
    return {"timmar": timmar, "upparbetat": upparbetat, "intakter": intakter, "kostnader": kostnader,
            "per_konto": dict(per_konto), "tim_per_uppdrag": dict(tim_per_uppdrag)}


def uppfoljning(budget, idag):
    b = budget.per_manad()
    u = utfall(budget.ar)
    if budget.ar < idag.year:
        stangda = 12
    elif budget.ar > idag.year:
        stangda = 0
    else:
        stangda = idag.month - 1  # avslutade månader

    def prognos(utfall_lista, budget_lista):
        # avslutade månader: utfall; innevarande: det största av utfall och budget; resten: budget
        res = []
        for i in range(12):
            if i < stangda:
                res.append(utfall_lista[i])
            elif i == stangda and budget.ar == idag.year:
                res.append(max(utfall_lista[i], budget_lista[i]))
            else:
                res.append(budget_lista[i])
        return res

    # Intäktsprognos utgår från det som är upparbetat (tid) eller fakturerat, det som är störst per månad
    intakt_utfall = [max(a, f) for a, f in zip(u["upparbetat"], u["intakter"])]
    p_intakter = prognos(intakt_utfall, b["intakter"])
    p_kostnader = prognos(u["kostnader"], b["kostnader"])

    def ack(lista):
        s, res = NOLL, []
        for v in lista:
            s += v
            res.append(s)
        return res

    rader = []
    for nyckel, namn in [("timmar", "Timmar"), ("intakter", "Intäkter"), ("kostnader", "Kostnader")]:
        rader.append({"nyckel": nyckel, "namn": namn, "budget": b[nyckel],
                      "budget_sum": sum(b[nyckel], NOLL)})
    resultat_budget = [i - k for i, k in zip(b["intakter"], b["kostnader"])]
    resultat_utfall = [i - k for i, k in zip(u["intakter"], u["kostnader"])]
    resultat_prognos = [i - k for i, k in zip(p_intakter, p_kostnader)]

    # per rad: utfall för uppdragsrader och kontorader
    radlista = []
    for r in budget.rader.select_related("uppdrag", "konto", "kund"):
        if r.typ == Budgetrad.TID and r.uppdrag_id:
            uf = u["tim_per_uppdrag"].get(r.uppdrag_id, [NOLL] * 12)
        elif r.typ == Budgetrad.KOSTNAD and r.konto_id:
            uf = u["per_konto"].get(r.konto_id, [NOLL] * 12)
        else:
            uf = None
        radlista.append({"rad": r, "utfall": uf, "utfall_sum": sum(uf, NOLL) if uf else None})

    hittills_budget = sum(b["intakter"][:stangda], NOLL)
    hittills_utfall = sum(intakt_utfall[:stangda], NOLL)
    return {
        "budget": b, "utfall": u, "stangda": stangda, "rader": rader, "radlista": radlista,
        "intakt_utfall": intakt_utfall,
        "prognos": {"intakter": p_intakter, "kostnader": p_kostnader, "resultat": resultat_prognos},
        "resultat": {"budget": resultat_budget, "utfall": resultat_utfall},
        "summa": {
            "budget_intakter": sum(b["intakter"], NOLL), "prognos_intakter": sum(p_intakter, NOLL),
            "budget_kostnader": sum(b["kostnader"], NOLL), "prognos_kostnader": sum(p_kostnader, NOLL),
            "budget_resultat": sum(resultat_budget, NOLL), "prognos_resultat": sum(resultat_prognos, NOLL),
            "budget_timmar": sum(b["timmar"], NOLL), "utfall_timmar": sum(u["timmar"], NOLL),
            "hittills_budget": hittills_budget, "hittills_utfall": hittills_utfall,
            "takt": (hittills_utfall / hittills_budget * 100) if hittills_budget else None,
        },
        "ack": {"budget": ack(b["intakter"]), "utfall": ack(intakt_utfall[:stangda] if stangda < 12 else intakt_utfall),
                "prognos": ack(p_intakter)},
    }


# ------------------------------------------------------------------ förslag

def skapa_forslag(budget, idag, belaggning=Decimal("90"), timmar_per_dag=Decimal("8"), kostnader_fran="forra"):
    """Fyller en ny budget med rader för aktiva uppdrag och kostnader utifrån historiken."""
    dagar = arbetsdagar(budget.ar)
    forsta = idag.month if budget.ar == idag.year else 1
    if budget.ar < idag.year:
        forsta = 1
    ordning = 0
    for u in Uppdrag.objects.filter(aktiv=True).select_related("kund", "artikel"):
        varden = []
        for m in range(1, 13):
            start = dt.date(budget.ar, m, 1)
            aktiv = m >= forsta and (not u.slutdatum or u.slutdatum >= start) and \
                (not u.startdatum or u.startdatum.replace(day=1) <= start)
            varden.append((Decimal(dagar[m - 1]) * timmar_per_dag * belaggning / 100).quantize(Decimal("1"))
                          if aktiv else NOLL)
        ordning += 1
        r = Budgetrad(budget=budget, typ=Budgetrad.TID, beskrivning=str(u), uppdrag=u, kund=u.kund,
                      timpris=u.pris, ordning=ordning)
        r.varden = varden
        r.save()

    # Kostnader: senaste 12 månadernas utfall per konto, som månadssnitt
    till = idag.replace(day=1) - dt.timedelta(days=1)
    fran = (till.replace(day=1) - dt.timedelta(days=330)).replace(day=1)
    qs = Transaktion.objects.filter(verifikation__datum__range=(fran, till), konto__nummer__gte=4000,
                                    konto__nummer__lt=9000).exclude(konto__nummer__in=[7399, 8999])
    manader = max(1, (till.year - fran.year) * 12 + till.month - fran.month + 1)
    for k, s in qs.values_list("konto").annotate(s=Sum("belopp")).values_list("konto", "s"):
        snitt = (_d(s) / manader).quantize(Decimal("1"))
        if snitt <= 0:
            continue
        ordning += 1
        from register.models import Konto
        konto = Konto.objects.get(pk=k)
        r = Budgetrad(budget=budget, typ=Budgetrad.KOSTNAD, beskrivning=konto.namn, konto=konto, ordning=ordning)
        r.varden = [snitt if m >= forsta else NOLL for m in range(1, 13)]
        r.save()


def kopiera(fran_budget, till_budget):
    for r in fran_budget.rader.all():
        varden = r.varden
        r.pk = None
        r.budget = till_budget
        r.varden = varden
        r.save()
