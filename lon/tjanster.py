from collections import defaultdict
from decimal import Decimal

from django.db import transaction

from bokforing import tjanster as bok

from .models import LONEARTER, Lonekorning, Lonerad, Lonespec

KONTO_PERSONALSKATT = 2710
KONTO_AG_SKULD = 2730
KONTO_AG_KOSTNAD = 7510
KONTO_FORMAN_MOT = 7399
KONTO_BANK = 1930
LONEKONTO_ARTER = {"manadslon", "timlon", "overtid", "bonus", "franvaro"}


def standardrader(spec):
    a = spec.anstalld
    if a.lonetyp == a.MANAD:
        Lonerad.objects.create(spec=spec, typ="manadslon", beskrivning="Månadslön", antal=1, a_pris=a.manadslon)
    else:
        Lonerad.objects.create(spec=spec, typ="timlon", beskrivning="Timlön", antal=0, a_pris=a.timlon)


def kopiera_fasta_rader(spec, forra):
    """Tar med förmåner från förra lönen (de brukar vara samma varje månad)."""
    for r in forra.rader.filter(typ__in=["formansbil", "forman"]):
        Lonerad.objects.create(spec=spec, typ=r.typ, beskrivning=r.beskrivning, antal=r.antal, a_pris=r.a_pris)


def uppdatera_skatt(spec):
    if spec.skatt_manuell:
        return
    forslag = spec.foreslagen_skatt()
    if forslag is not None:
        Lonespec.objects.filter(pk=spec.pk).update(skatteavdrag=forslag)
        spec.skatteavdrag = forslag


def verifikationsrader(korning):
    rader = defaultdict(Decimal)
    avgift_tot = Decimal(0)
    for spec in korning.specar.select_related("anstalld").prefetch_related("rader"):
        a, b = spec.anstalld, spec.berakna()
        ks = a.kostnadsstalle
        for r in spec.rader.all():
            art = LONEARTER[r.typ]
            konto = (a.loneskonto_id or art[1]) if r.typ in LONEKONTO_ARTER else art[1]
            rader[(konto, ks)] += r.belopp
            if art[2] and not art[4]:  # förmån: motbokas, påverkar bara skatt/avgift
                rader[(KONTO_FORMAN_MOT, ks)] -= r.belopp
        rader[(KONTO_PERSONALSKATT, None)] -= b["skatt"]
        rader[(KONTO_BANK, None)] -= b["netto"]
        rader[(KONTO_AG_KOSTNAD, ks)] += b["avgift"]
        avgift_tot += b["avgift"]
    rader[(KONTO_AG_SKULD, None)] -= avgift_tot
    return [{"konto": k, "belopp": v, "kostnadsstalle": ks} for (k, ks), v in rader.items() if v]


@transaction.atomic
def bokfor_lonekorning(korning, anvandare=""):
    if korning.last:
        raise bok.BokforingsFel("Lönekörningen är redan bokförd.")
    if not korning.specar.exists():
        raise bok.BokforingsFel("Lönekörningen har inga lönespecifikationer.")
    for spec in korning.specar.all():
        if spec.berakna()["netto"] < 0:
            raise bok.BokforingsFel(f"Negativ nettolön för {spec.anstalld}.")
    ver = bok.bokfor(korning.utbetalningsdatum, f"Löner {korning.period}", verifikationsrader(korning),
                     serie="S", anvandare=anvandare, lonekorning=korning)
    Lonekorning.objects.filter(pk=korning.pk).update(status=Lonekorning.BOKFORD)
    korning.status = Lonekorning.BOKFORD
    return ver


@transaction.atomic
def las_upp(korning, datum, anvandare=""):
    for v in bok.aktiva_ver(korning.verifikationer.all()):
        bok.rattelse(v, datum, anvandare, text=f"Rättelse av {v.vernr}: {v.text}")
    Lonekorning.objects.filter(pk=korning.pk).update(status=Lonekorning.UTKAST)
    korning.status = Lonekorning.UTKAST


def agi(period):
    """Sammanställning för arbetsgivardeklarationen (redovisningsperiod = utbetalningsmånad)."""
    ar, man = (int(x) for x in period.split("-"))
    korningar = Lonekorning.objects.filter(utbetalningsdatum__year=ar, utbetalningsdatum__month=man,
                                           status=Lonekorning.BOKFORD)
    individer = defaultdict(lambda: {"011": Decimal(0), "012": Decimal(0), "013": Decimal(0), "001": Decimal(0),
                                     "skattefritt": Decimal(0), "avgift": Decimal(0), "grupp": ""})
    grupper = defaultdict(lambda: {"underlag": Decimal(0), "avgift": Decimal(0)})
    for k in korningar:
        for spec in k.specar.select_related("anstalld").prefetch_related("rader"):
            b = spec.berakna()
            i = individer[spec.anstalld]
            for ruta in ("011", "012", "013"):
                i[ruta] += b["rutor"][ruta]
            i["001"] += b["skatt"]
            i["skattefritt"] += b["skattefritt"]
            i["avgift"] += b["avgift"]
            i["grupp"] = b["avgiftsgrupp"]
            grupper[b["avgiftsgrupp"]]["underlag"] += b["underlag"]
            grupper[b["avgiftsgrupp"]]["avgift"] += b["avgift"]
    return {
        "korningar": korningar, "individer": dict(individer), "grupper": dict(grupper),
        "summa_avgift": sum((g["avgift"] for g in grupper.values()), Decimal(0)),
        "summa_skatt": sum((i["001"] for i in individer.values()), Decimal(0)),
    }
