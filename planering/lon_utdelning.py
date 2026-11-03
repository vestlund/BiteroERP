"""Lön eller utdelning: räkna fram fördelningen som ger störst nettobelopp till ägaren.

Modell (ett "normalår", samma lön varje år):
- Bolagets resultat före ägarens lön (R) är en prognos. Lönen L kostar L × (1 + arbetsgivaravgift).
- Bolagsskatt 20,6 % på resten (avrundat nedåt till hela hundratal). Det som blir kvar, plus eventuellt fritt
  eget kapital från tidigare år, får delas ut. Utdelningen av årets vinst betalas året efter.
- Gränsbelopp (3:12-reglerna från 2026): grundbelopp 4 IBB × ägarandel + lönebaserat utrymme 50 % ×
  (löner × ägarandel − 8 IBB), högst 50 × egen lön, + sparat utrymme. Lönerna avser året före utdelningsåret,
  vilket i ett normalår är samma lön som i kalkylen.
- Utdelning inom gränsbeloppet: 20 % skatt. Över gränsbeloppet: inkomst av tjänst (ingen arbetsgivaravgift,
  inget jobbskatteavdrag, ingen pensionsrätt) upp till 90 IBB, därefter 30 %.
- Lön och tjänstebeskattad utdelning: kommunalskatt, statlig skatt över skiktgränsen, grundavdrag,
  jobbskatteavdrag, skattereduktion för förvärvsinkomst och allmän pensionsavgift (som kvittas med en lika stor
  skattereduktion), enligt Skatteverkets tekniska beskrivning SKV 433 för inkomståret 2026.

Förenklingar: personen har inte fyllt 66 år, har inga andra inkomster, ingen kyrkoavgift utöver det som anges
som avgift, inget räntebaserat utrymme och ingen tjänstepension. Resultatet är ett beslutsunderlag, inte en
deklaration.
"""

import math
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from .skatt import BOLAGSSKATT, gransbelopp, ibb

NOLL = Decimal(0)
D = Decimal

# Belopp per inkomstår (Skatteverket, Belopp och procent 2026, SKV 433 utgåva 36)
PARAMETRAR = {
    2026: {"pbb": D(59200), "ibb": D(83400), "skiktgrans": D(643000)},
}
STATLIG = D("0.20")
PENSIONSAVGIFT = D("0.07")
PENSIONSTAK_IBB = D("8.07")
UTDELNING_KAPITAL = D("0.20")
UTDELNING_OVER_TAK = D("0.30")
TAK_TJANST_IBB = 90


def parametrar(ar):
    """Parametrar för inkomståret, eller senast kända. Returnerar (parametrar, år som används)."""
    kanda = [a for a in PARAMETRAR if a <= ar] or [min(PARAMETRAR)]
    a = max(kanda)
    return PARAMETRAR[a], a


def _hundra_upp(v):
    return D(math.ceil(v / 100) * 100)


def _helt(v):
    return D(v).quantize(D(1), rounding=ROUND_DOWN)


def grundavdrag(ffi, pbb):
    """Grundavdrag för den som inte fyllt 66 år (SKV 433 2026, avsnitt 7.3)."""
    if ffi <= 0:
        return NOLL
    p = ffi / pbb
    if p <= D("0.99"):
        ga = D("0.423") * pbb
    elif p <= D("2.72"):
        ga = D("0.423") * pbb + D("0.20") * (ffi - D("0.99") * pbb)
    elif p <= D("3.11"):
        ga = D("0.77") * pbb
    elif p <= D("7.88"):
        ga = D("0.77") * pbb - D("0.10") * (ffi - D("3.11") * pbb)
    else:
        ga = D("0.293") * pbb
    return min(_hundra_upp(ga), _hundra_upp(ffi))


def jobbskatteavdrag(ai, ga, ki, pbb):
    """Skattereduktion för arbetsinkomst (under 66 år). ki = kommunal skattesats som andel, t.ex. 0.3238."""
    if ai <= 0:
        return NOLL
    if ai <= D("0.91") * pbb:
        bas = ai
    elif ai <= D("3.24") * pbb:
        bas = D("0.91") * pbb + D("0.3874") * (ai - D("0.91") * pbb)
    elif ai <= D("8.08") * pbb:
        bas = D("1.813") * pbb + D("0.251") * (ai - D("3.24") * pbb)
    else:
        bas = D("3.027") * pbb
    return max(NOLL, (bas - ga) * ki)


def inkomstskatt(lon, tjanst_utdelning, kommunalskatt, avgift=NOLL, ar=2026):
    """Skatt på lön och tjänstebeskattad utdelning ett år. Procentsatser anges i procent (32.38)."""
    p, _ = parametrar(ar)
    pbb, ibb_ar = p["pbb"], p["ibb"]
    ki = D(kommunalskatt) / 100
    ffi = _helt(lon + tjanst_utdelning)
    ga = grundavdrag(ffi, pbb)
    bfi = max(NOLL, ffi - ga)
    kommunal = bfi * ki
    begravning = bfi * D(avgift) / 100
    statlig = max(NOLL, bfi - p["skiktgrans"]) * STATLIG
    jsa = min(jobbskatteavdrag(_helt(lon), ga, ki, pbb), kommunal)
    forvarv = min(D(1500), max(NOLL, (bfi - 40000) * D("0.0075"))) if bfi > 40000 else NOLL
    forvarv = min(forvarv, kommunal - jsa)
    pensionsunderlag = min(_helt(lon), PENSIONSTAK_IBB * ibb_ar)
    pension = D(round(pensionsunderlag * PENSIONSAVGIFT / 100) * 100) if pensionsunderlag >= D("0.423") * pbb else NOLL
    pensionsreduktion = min(pension, max(NOLL, kommunal - jsa - forvarv + statlig))
    skatt = kommunal + statlig + begravning + pension - jsa - forvarv - pensionsreduktion
    return {"ffi": ffi, "grundavdrag": ga, "kommunal": kommunal, "statlig": statlig, "begravning": begravning,
            "jobbskatteavdrag": jsa, "forvarvsreduktion": forvarv, "pensionsavgift": pension,
            "skatt": skatt.quantize(D(1))}


SGI_TAK_PBB = 10  # högsta sjukpenninggrundande inkomst, även föräldrapenning (Försäkringskassan)


STANDARD_KOMMUNALSKATT, STANDARD_AVGIFT = D("32.38"), D("0.27")


def rekommenderad_lon(ar, arbetsgivaravgift, kommunalskatt=STANDARD_KOMMUNALSKATT, avgift=STANDARD_AVGIFT):
    """Lägsta lön för full SGI och full allmän pension, och vad den kostar bolaget.

    - Full SGI: 10 prisbasbelopp per år.
    - Full allmän pension: 8,07 inkomstbasbelopp per år (ger högsta pensionsgrundande inkomst, 7,5 IBB, efter
      avdrag för den allmänna pensionsavgiften).
    Den rekommenderade lägsta lönen är den högre av de två. Lön över den ger varken mer sjukpenning eller
    mer allmän pension.
    """
    p, p_ar = parametrar(ar)
    ag = D(arbetsgivaravgift) / 100

    def niva(namn, beskrivning, lon):
        lon = D(math.ceil(lon))
        avg = (lon * ag).quantize(D(1))
        skatt = inkomstskatt(lon, NOLL, kommunalskatt, avgift, ar)["skatt"]
        return {"namn": namn, "beskrivning": beskrivning, "lon": lon, "lon_manad": D(math.ceil(lon / 12)),
                "arbetsgivaravgift": avg, "kostnad": lon + avg, "kostnad_manad": D(math.ceil((lon + avg) / 12)),
                "netto": lon - skatt, "netto_manad": ((lon - skatt) / 12).quantize(D(1))}
    def kr(v):
        return f"{v:,.0f}".replace(",", " ")
    sgi = niva("Full SGI", f"{SGI_TAK_PBB} × prisbasbeloppet {kr(p['pbb'])} kr", SGI_TAK_PBB * p["pbb"])
    pension = niva("Full allmän pension", f"8,07 × inkomstbasbeloppet {kr(p['ibb'])} kr", PENSIONSTAK_IBB * p["ibb"])
    rek = max(sgi, pension, key=lambda n: n["lon"])
    brytpunkt = p["skiktgrans"] + D("0.293") * p["pbb"]
    return {"ar": p_ar, "sgi": sgi, "pension": pension, "nivaer": [sgi, pension], "rekommenderad": rek, "arbetsgivaravgift": D(arbetsgivaravgift),
            "brytpunkt": _hundra_upp(brytpunkt), "over_brytpunkt": rek["lon"] > brytpunkt}


@dataclass
class Forutsattningar:
    ar: int
    resultat_fore_agarlon: Decimal
    kommunalskatt: Decimal = D("32.38")
    avgift: Decimal = D("0.27")
    arbetsgivaravgift: Decimal = D("31.42")
    agarandel: Decimal = D(100)
    ovriga_loner: Decimal = NOLL
    sparat: Decimal = NOLL
    fritt_eget_kapital: Decimal = NOLL
    max_uttag: Decimal | None = None
    max_inklusive_avgifter: bool = False
    min_lon: Decimal = NOLL
    bara_inom_gransbelopp: bool = False


def alternativ(f: Forutsattningar, lon, utdelning=None):
    """Räknar ut ett alternativ. Utan angiven utdelning tas så mycket som villkoren tillåter."""
    lon = D(lon)
    ag = (lon * f.arbetsgivaravgift / 100).quantize(D(1))
    lonekostnad = lon + ag
    fore_skatt = f.resultat_fore_agarlon - lonekostnad
    bolagsskatt = _helt(max(NOLL, _helt(fore_skatt / 100) * 100) * BOLAGSSKATT)
    vinst = fore_skatt - bolagsskatt
    utdelningsbart = max(NOLL, vinst + f.fritt_eget_kapital)
    gb = gransbelopp(f.ar + 1, f.agarandel, loneunderlag=lon + f.ovriga_loner, egen_lon=lon, sparat=f.sparat)
    tak = None
    if f.max_uttag is not None:
        tak = f.max_uttag - (lonekostnad if f.max_inklusive_avgifter else lon)
    if utdelning is None:
        utdelning = utdelningsbart
        if tak is not None:
            utdelning = min(utdelning, tak)
        if f.bara_inom_gransbelopp:
            utdelning = min(utdelning, gb["summa"])
    utdelning = max(NOLL, _helt(utdelning))
    inom = min(utdelning, gb["summa"])
    p, p_ar = parametrar(f.ar + 1)
    tjanst = min(utdelning - inom, TAK_TJANST_IBB * p["ibb"])
    over_tak = utdelning - inom - tjanst
    skatt_lon = inkomstskatt(lon, tjanst, f.kommunalskatt, f.avgift, f.ar)
    skatt_utan_utd = inkomstskatt(lon, NOLL, f.kommunalskatt, f.avgift, f.ar)["skatt"]
    skatt_tjanst_utd = skatt_lon["skatt"] - skatt_utan_utd
    skatt_kapital = (inom * UTDELNING_KAPITAL + over_tak * UTDELNING_OVER_TAK).quantize(D(1))
    netto_lon = lon - skatt_utan_utd
    netto_utd = utdelning - skatt_tjanst_utd - skatt_kapital
    netto = netto_lon + netto_utd
    totala_skatter = ag + bolagsskatt + skatt_lon["skatt"] + skatt_kapital
    kvar = utdelningsbart - utdelning
    return {
        "lon": lon, "arbetsgivaravgift": ag, "lonekostnad": lonekostnad, "bolagsskatt": bolagsskatt,
        "vinst_efter_skatt": vinst, "utdelningsbart": utdelningsbart, "gransbelopp": gb, "utdelning": utdelning,
        "utdelning_inom": inom, "utdelning_tjanst": tjanst, "utdelning_over_tak": over_tak,
        "skatt_lon": skatt_utan_utd, "skatt_utdelning": skatt_tjanst_utd + skatt_kapital,
        "inkomstskatt": skatt_lon, "netto_lon": netto_lon, "netto_utdelning": netto_utd, "netto": netto,
        "uttag": lon + utdelning, "lon_manad": lon / 12, "netto_manad": netto / 12, "uttag_inkl_avgifter": lonekostnad + utdelning, "kvar_i_bolaget": kvar,
        "totala_skatter": totala_skatter,
        "skattekvot": (totala_skatter / (netto + totala_skatter) * 100).quantize(D("0.1"))
        if netto + totala_skatter > 0 else NOLL,
        "pensionsgrundande": min(lon, PENSIONSTAK_IBB * p["ibb"]), "parameterar": p_ar,
        "mojlig": fore_skatt + f.fritt_eget_kapital >= 0 and (tak is None or tak >= 0),
    }


def optimera(f: Forutsattningar, steg=1000):
    """Provar löner i steg om `steg` kr och väljer den med störst netto till ägaren inom villkoren.

    Lika netto: lägst totalt uttag (mer kvar i bolaget) vinner.
    """
    ag = 1 + f.arbetsgivaravgift / 100
    hogsta = max(NOLL, (f.resultat_fore_agarlon + f.fritt_eget_kapital) / ag)
    if f.max_uttag is not None:
        hogsta = min(hogsta, f.max_uttag / ag if f.max_inklusive_avgifter else f.max_uttag)
    hogsta = _helt(hogsta)
    p, _ = parametrar(f.ar)
    # steg + brytpunkter där marginalskatten eller gränsbeloppet ändras
    kandidater = {D(x) for x in range(int(f.min_lon), int(hogsta) + 1, steg)} | {hogsta, D(f.min_lon)}
    for v in (p["skiktgrans"] + D("0.293") * p["pbb"], 8 * ibb(f.ar) / (f.agarandel / 100) - f.ovriga_loner,
              PENSIONSTAK_IBB * p["ibb"], D("8.08") * p["pbb"]):
        v = _hundra_upp(v)
        if f.min_lon <= v <= hogsta:
            kandidater.add(v)
    alla = [alternativ(f, lon) for lon in sorted(kandidater) if f.min_lon <= lon <= hogsta]
    alla = [a for a in alla if a["mojlig"]]
    if not alla:
        return None, []
    bast = max(alla, key=lambda a: (a["netto"], -a["uttag_inkl_avgifter"]))
    return bast, alla


def jamforelse(f: Forutsattningar, bast, alla, antal=8):
    """Ett urval alternativ att visa i en tabell: jämnt fördelade löner plus det bästa."""
    if not alla:
        return []
    idx = {round(i * (len(alla) - 1) / (antal - 1)) for i in range(antal)} if len(alla) > antal else set(range(len(alla)))
    urval = [alla[i] for i in sorted(idx)]
    if bast not in urval:
        urval.append(bast)
    return sorted(urval, key=lambda a: a["lon"])


# ------------------------------------------------------------------ förifyllda värden från bokföringen

LONEKOSTNADER = (7000, 7699)  # löner, förmåner, arbetsgivaravgifter och pensionskostnader


def resultat_fore_lon(idag):
    """Prognos för årets resultat före lönekostnader: bokfört + budget resten av året, med lönerna återlagda."""
    import datetime as dt

    from bokforing import tjanster as bok

    from .skatt import skatteprognos
    pr = skatteprognos(idag)
    s = bok.saldon(dt.date(idag.year, 1, 1), idag)
    loner_hittills = sum((v["period"] for k, v in s.items() if LONEKOSTNADER[0] <= k <= LONEKOSTNADER[1]), NOLL)
    loner_budget = NOLL
    try:
        from budget.models import Budget, Budgetrad
        bud = Budget.objects.filter(ar=idag.year, huvudbudget=True).first()
        if bud:
            for rad in bud.rader.filter(typ=Budgetrad.KOSTNAD, konto__nummer__range=LONEKOSTNADER):
                loner_budget += sum((getattr(rad, f"m{m}") for m in range(idag.month + 1, 13)), NOLL)
    except Exception:  # noqa: BLE001 - utan budget används bara det bokförda
        pass
    return {"resultat": _helt(pr["prognos"] + loner_hittills + loner_budget), "prognos": pr["prognos"],
            "loner_hittills": loner_hittills, "loner_budget": loner_budget,
            "loner_tillbaka": loner_hittills + loner_budget, "budget": pr["budget"]}


def agarens_arbetsgivaravgift(ar):
    """Arbetsgivaravgift för den första aktiva anställda (oftast ägaren), annars full avgift."""
    import datetime as dt

    from lon.models import Anstalld, avgiftsgrupp
    a = Anstalld.objects.filter(aktiv=True).order_by("nummer").first()
    return avgiftsgrupp(a.fodelsear if a else None, dt.date(ar, 6, 30))[1]
