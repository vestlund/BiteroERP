"""Skatteprognos för bolaget och utdelningsutrymme enligt 3:12-reglerna (från 2026).

Bolagsskatt: 20,6 % på prognostiserat resultat (bokfört hittills + budget för resten av året), jämfört
med debiterad F-skatt.

Gränsbelopp för utdelning ett beskattningsår (3:12-reglerna från 1 januari 2026):
- grundbelopp: 4 inkomstbasbelopp för året före beskattningsåret, fördelat efter ägarandel,
- lönebaserat utrymme: 50 % av (bolagets kontanta löner året före × ägarandel − 8 inkomstbasbelopp),
  högst 50 gånger delägarens egen lön,
- sparat utdelningsutrymme från tidigare år (räknas inte längre upp med ränta),
- ränta på omkostnadsbelopp över 100 000 kr ingår inte här.
Utdelning inom gränsbeloppet beskattas med 20 %. Källor: Skatteverket, Wolters Kluwer, Björn Lundén (2026).
"""

import datetime as dt
from decimal import ROUND_DOWN, Decimal

from bokforing import tjanster as bok
from register.models import Foretag

NOLL = Decimal(0)
BOLAGSSKATT = Decimal("0.206")
UTDELNINGSSKATT = Decimal("0.20")
INKOMSTBASBELOPP = {2023: 74300, 2024: 76200, 2025: 80600, 2026: 83400}


def ibb(ar):
    if ar in INKOMSTBASBELOPP:
        return Decimal(INKOMSTBASBELOPP[ar])
    return Decimal(INKOMSTBASBELOPP[max(INKOMSTBASBELOPP)])


def _helt(v):
    return Decimal(v).quantize(Decimal(1), rounding=ROUND_DOWN)


def skatteprognos(idag):
    """Prognos för innevarande år: resultat, bolagsskatt och jämförelse med debiterad F-skatt."""
    ar = idag.year
    foretag = Foretag.hamta()
    s = bok.saldon(dt.date(ar, 1, 1), idag)
    hittills = -sum((v["period"] for k, v in s.items() if 3000 <= k <= 8899), NOLL)
    rest = NOLL
    budget_namn = None
    try:
        from budget.models import Budget
        bud = Budget.objects.filter(ar=ar, huvudbudget=True).first()
        if bud:
            pm = bud.per_manad()
            for i in range(idag.month, 12):  # hela kommande månader
                rest += pm["intakter"][i] - pm["kostnader"][i]
            budget_namn = str(bud)
    except Exception:
        pass
    prognos = hittills + rest
    skatt = _helt(max(NOLL, _helt(prognos / 100) * 100) * BOLAGSSKATT)
    fskatt = (foretag.fskatt_manad or NOLL) * 12
    return {"ar": ar, "hittills": hittills, "rest": rest, "prognos": prognos, "budget": budget_namn,
            "skatt": skatt, "fskatt": fskatt, "fskatt_manad": foretag.fskatt_manad, "skillnad": skatt - fskatt,
            "efter_skatt": prognos - skatt, "fyllnad_senast": dt.date(ar + 1, 2, 12)}


def loner(ar, konton=(7000, 7299)):
    """Kontant lön under ett år enligt bokföringen (lönekonton 7000–7299)."""
    s = bok.saldon(dt.date(ar, 1, 1), dt.date(ar, 12, 31))
    return sum((v["period"] for k, v in s.items() if konton[0] <= k <= konton[1]), NOLL)


def fritt_eget_kapital(ar):
    """Fritt eget kapital vid utgången av året (2090–2099 inklusive årets resultat).

    Finns ingående balanser för året efter (t.ex. från Visma) används de. Balanserar de inte beror det
    nästan alltid på att årets resultat inte är bokat mot eget kapital än; skillnaden räknas då som resultat.
    """
    from bokforing.models import IngaendeBalans
    ib = {b.konto_id: b.belopp for b in IngaendeBalans.objects.filter(ar=ar + 1)}
    if ib:
        fritt = -sum((b for k, b in ib.items() if 2090 <= k <= 2099), NOLL)
        return fritt + sum(ib.values(), NOLL)
    s = bok.saldon(dt.date(ar, 1, 1), dt.date(ar, 12, 31))
    fritt = -sum((v["ub"] for k, v in s.items() if 2090 <= k <= 2099), NOLL)
    if not any(k == 2099 and v["ub"] for k, v in s.items()):  # årets resultat inte bokfört än
        fritt += -sum((v["period"] for k, v in s.items() if 3000 <= k <= 8989), NOLL)
    return fritt


def gransbelopp(beskattningsar, agarandel=Decimal(100), loneunderlag=None, egen_lon=None, sparat=NOLL):
    """Årets gränsbelopp för en delägare. Löneunderlag och egen lön avser året före beskattningsåret."""
    andel = Decimal(agarandel) / 100
    i = ibb(beskattningsar - 1)
    grund = _helt(4 * i * andel)
    if loneunderlag is None:
        loneunderlag = loner(beskattningsar - 1)
    efter_avdrag = max(NOLL, loneunderlag * andel - 8 * i)
    lonebaserat = _helt(efter_avdrag * Decimal("0.5"))
    tak = None
    if egen_lon is not None:
        tak = _helt(50 * egen_lon)
        lonebaserat = min(lonebaserat, tak)
    summa = grund + lonebaserat + Decimal(sparat or 0)
    return {"ar": beskattningsar, "ibb": i, "grundbelopp": grund, "loneunderlag": loneunderlag,
            "loneavdrag": 8 * i, "lonebaserat": lonebaserat, "tak": tak, "sparat": Decimal(sparat or 0),
            "summa": summa, "skatt": _helt(summa * UTDELNINGSSKATT), "netto": summa - _helt(summa * UTDELNINGSSKATT)}


def utdelning(idag, agarandel=None, egen_lon=None):
    """Utdelningsutrymme i år (utdelning beslutad på årsstämman) och nästa år."""
    foretag = Foretag.hamta()
    andel = agarandel if agarandel is not None else foretag.agarandel
    ar = idag.year
    i_ar = gransbelopp(ar, andel, egen_lon=egen_lon, sparat=foretag.sparat_utdelningsutrymme)
    fritt_fg = fritt_eget_kapital(ar - 1)
    nasta = gransbelopp(ar + 1, andel, loneunderlag=loner(ar) + _prognos_loner_rest(idag), egen_lon=egen_lon)
    return {"i_ar": i_ar, "nasta": nasta, "fritt_eget_kapital": fritt_fg,
            "max_i_ar": max(NOLL, min(i_ar["summa"], fritt_fg)), "agarandel": andel}


def _prognos_loner_rest(idag):
    """Resten av årets löner, uppskattat som senaste bokförda lönemånaden × återstående månader."""
    from planering.likviditet import _manad, lon_bokford
    m_ar, m = idag.year, idag.month
    for _ in range(4):
        if lon_bokford(m_ar, m):
            senaste = Decimal(str(_manad(m_ar, m, (7000, 7299)) or 0))
            kvar = 12 - m if m_ar == idag.year else 12
            return senaste * kvar
        m_ar, m = (m_ar - (m == 1), (m - 2) % 12 + 1)
    return NOLL
