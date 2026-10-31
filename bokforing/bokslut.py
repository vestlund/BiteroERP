"""Bokslut: checklista, periodiseringar, avskrivningar, skatt, årets resultat och underlag till
inkomstdeklarationen (INK2R/INK2S).

Bokslutsverifikationerna bokförs i serie A per den 31 december med texten "Bokslut <år>: ...", så att
de går att känna igen och inte bokförs två gånger. Periodiseringar återförs automatiskt den 1 januari.
"""

import datetime as dt
from decimal import ROUND_DOWN, Decimal

from config.pdf import kr
from register.models import Foretag

from . import tjanster as bok
from .models import Bankpost, Inventarie, Verifikation

NOLL = Decimal(0)
BOLAGSSKATT = Decimal("0.206")

# Konton vars kostnader inte är avdragsgilla (BAS): läggs tillbaka i INK2S 4.3c
EJ_AVDRAGSGILLA = {
    6072: "Representation, ej avdragsgill", 6342: "Gåvor och donationer, ej avdragsgilla",
    6982: "Föreningsavgifter, ej avdragsgilla", 6992: "Övriga externa kostnader, ej avdragsgilla",
    7622: "Sjuk- och hälsovård, ej avdragsgill", 7632: "Personalrepresentation, ej avdragsgill",
    8423: "Räntekostnader för skatter och avgifter", 7623: "Sjukvårdsförsäkring, ej avdragsgill",
}

PERIODISERINGAR = {
    "upplupen_intakt": ("Upplupen intäkt (upparbetat, inte fakturerat)", 1790, +1),
    "forutbetald_kostnad": ("Förutbetald kostnad (betalt för nästa år)", 1790, +1),
    "upplupen_kostnad": ("Upplupen kostnad (kostnad i år, faktura nästa år)", 2990, -1),
    "forutbetald_intakt": ("Förutbetald intäkt (fakturerat för nästa år)", 2990, -1),
}


def bokslutsdag(ar):
    return dt.date(ar, 12, 31)


def _bokslutsver(ar, rubrik):
    return bok.aktiva_ver(Verifikation.objects.filter(ar=ar, text__startswith=f"Bokslut {ar}: {rubrik}")).first()


def _r(v):
    return Decimal(v).quantize(Decimal("0.01"))


# ------------------------------------------------------------------ periodiseringar

def upparbetat_ej_fakturerat(ar):
    """Tid t.o.m. 31/12 som inte var fakturerad på bokslutsdagen (ofakturerad, eller fakturerad efter årsskiftet)."""
    from verksamhet.models import Tidpost
    slut = bokslutsdag(ar)
    summa = NOLL
    for t in Tidpost.objects.filter(datum__lte=slut, datum__year=ar, fakturerbar=True).select_related(
            "uppdrag__artikel", "fakturarad__faktura"):
        if t.fakturarad_id is None:
            summa += t.timmar * t.uppdrag.pris
        elif t.fakturarad.faktura.fakturadatum > slut:
            summa += t.timmar * t.fakturarad.a_pris
    return _r(summa)


def bokfor_periodisering(ar, typ, resultatkonto, belopp, text, anvandare=""):
    """Bokför en periodisering per 31/12 och återföringen per 1/1 året efter. Returnerar (ver, återföring)."""
    namn, balanskonto, tecken = PERIODISERINGAR[typ]
    belopp = _r(belopp)
    if belopp <= 0:
        raise bok.BokforingsFel("Ange ett belopp större än noll.")
    rubrik = f"Bokslut {ar}: {namn.split(' (')[0]}" + (f" – {text}" if text else "")
    rader = [(balanskonto, tecken * belopp), (resultatkonto, -tecken * belopp)]
    ver = bok.bokfor(bokslutsdag(ar), rubrik, rader, serie="A", anvandare=anvandare)
    ater = bok.bokfor(dt.date(ar + 1, 1, 1), f"Återföring: {rubrik}", [(k, -b) for k, b in rader], serie="A",
                      anvandare=anvandare)
    return ver, ater


# ------------------------------------------------------------------ avskrivningar

def avskrivningar(ar):
    rader = []
    for inv in Inventarie.objects.filter(anskaffningsdatum__year__lte=ar):
        belopp = inv.avskrivning(ar)
        if belopp:
            rader.append({"inventarie": inv, "belopp": belopp, "restvarde": inv.bokfort_varde(ar)})
    return rader


def bokfor_avskrivningar(ar, anvandare=""):
    if _bokslutsver(ar, "Avskrivningar"):
        raise bok.BokforingsFel(f"Avskrivningarna för {ar} är redan bokförda.")
    rader = []
    for r in avskrivningar(ar):
        inv = r["inventarie"]
        rader += [{"konto": inv.avskrivningskonto_id, "belopp": r["belopp"], "text": inv.namn},
                  {"konto": inv.ack_konto_id, "belopp": -r["belopp"], "text": inv.namn}]
    if not rader:
        raise bok.BokforingsFel("Det finns inga avskrivningar att bokföra.")
    return bok.bokfor(bokslutsdag(ar), f"Bokslut {ar}: Avskrivningar inventarier enligt plan", rader,
                      serie="A", anvandare=anvandare)


# ------------------------------------------------------------------ skatt och resultat

def resultat(ar, till_och_med_konto=8899):
    s = bok.saldon(dt.date(ar, 1, 1), bokslutsdag(ar))
    return -sum((v["period"] for k, v in s.items() if 3000 <= k <= till_och_med_konto), NOLL), s


def skatteberakning(ar, ovriga_justeringar=NOLL):
    """Förenklad beräkning av bolagsskatt (20,6 %) enligt INK2S."""
    fore_skatt, s = resultat(ar)
    bokford_skatt = sum((v["period"] for k, v in s.items() if 8900 <= k <= 8989), NOLL)
    ej_avdr = [{"konto": k, "namn": n, "belopp": s[k]["period"]} for k, n in EJ_AVDRAGSGILLA.items()
               if k in s and s[k]["period"]]
    summa_ej_avdr = sum((r["belopp"] for r in ej_avdr), NOLL)
    overskott = fore_skatt + summa_ej_avdr + ovriga_justeringar
    underlag = max(NOLL, (overskott / 100).quantize(Decimal(1), rounding=ROUND_DOWN) * 100)  # hela hundratal
    skatt = (underlag * BOLAGSSKATT).quantize(Decimal(1), rounding=ROUND_DOWN)
    return {"fore_skatt": fore_skatt, "ej_avdragsgilla": ej_avdr, "summa_ej_avdragsgilla": summa_ej_avdr,
            "ovriga_justeringar": ovriga_justeringar, "overskott": overskott, "underlag": underlag,
            "skatt": skatt, "bokford_skatt": bokford_skatt, "efter_skatt": fore_skatt - skatt}


def bokfor_skatt(ar, belopp, anvandare=""):
    if _bokslutsver(ar, "Skatt på årets resultat"):
        raise bok.BokforingsFel(f"Skatten för {ar} är redan bokförd. Rätta verifikationen om beloppet ska ändras.")
    belopp = _r(belopp)
    if belopp <= 0:
        raise bok.BokforingsFel("Ingen skatt att bokföra.")
    return bok.bokfor(bokslutsdag(ar), f"Bokslut {ar}: Skatt på årets resultat", [(8910, belopp), (2512, -belopp)],
                      serie="A", anvandare=anvandare)


def bokfor_arets_resultat(ar, anvandare=""):
    if _bokslutsver(ar, "Årets resultat"):
        raise bok.BokforingsFel(f"Årets resultat för {ar} är redan bokfört.")
    res, _ = resultat(ar, till_och_med_konto=8989)
    if not res:
        raise bok.BokforingsFel("Resultatet är noll.")
    return bok.bokfor(bokslutsdag(ar), f"Bokslut {ar}: Årets resultat", [(8999, res), (2099, -res)],
                      serie="A", anvandare=anvandare)


# ------------------------------------------------------------------ INK2R/INK2S

def _i(*intervall):
    res = []
    for x in intervall:
        a, b = (x, x) if isinstance(x, int) else x
        res.append((a, b))
    return res


# (fältkod, punkt, namn, konton, sida) – sida "B" balans, "R" resultat. Källa: BAS kopplingstabell INK2 (P1).
INK2R = [
    ("7201", "2.1", "Koncessioner, patent, licenser, goodwill m.m.", _i((1000, 1087), (1089, 1099)), "B"),
    ("7202", "2.2", "Förskott immateriella anläggningstillgångar", _i(1088), "B"),
    ("7214", "2.3", "Byggnader och mark", _i((1100, 1119), (1130, 1179), (1190, 1199)), "B"),
    ("7215", "2.4", "Maskiner, inventarier och övriga materiella anläggningstillgångar", _i((1200, 1279), (1290, 1299)), "B"),
    ("7216", "2.5", "Förbättringsutgifter på annans fastighet", _i((1120, 1129)), "B"),
    ("7217", "2.6", "Pågående nyanläggningar och förskott", _i((1180, 1189), (1280, 1289)), "B"),
    ("7230", "2.7", "Andelar i koncernföretag", _i((1310, 1319)), "B"),
    ("7231", "2.8", "Andelar i intresseföretag", _i((1330, 1335), (1338, 1339)), "B"),
    ("7233", "2.9", "Ägarintressen i övriga företag och andra långfristiga värdepappersinnehav", _i((1350, 1359), 1336, 1337), "B"),
    ("7232", "2.10", "Fordringar hos koncern- och intresseföretag", _i((1320, 1329), (1340, 1345), (1348, 1349)), "B"),
    ("7234", "2.11", "Lån till delägare eller närstående", _i((1360, 1369)), "B"),
    ("7235", "2.12", "Övriga långfristiga fordringar", _i((1370, 1389), 1346, 1347), "B"),
    ("7241", "2.13", "Råvaror och förnödenheter", _i((1410, 1429)), "B"),
    ("7242", "2.14", "Varor under tillverkning", _i((1440, 1449)), "B"),
    ("7243", "2.15", "Färdiga varor och handelsvaror", _i((1450, 1469)), "B"),
    ("7244", "2.16", "Övriga lagertillgångar", _i((1490, 1499)), "B"),
    ("7245", "2.17", "Pågående arbeten för annans räkning", _i((1470, 1479)), "B"),
    ("7246", "2.18", "Förskott till leverantörer", _i((1480, 1489)), "B"),
    ("7251", "2.19", "Kundfordringar", _i((1510, 1559), (1580, 1589)), "B"),
    ("7252", "2.20", "Fordringar hos koncern- och intresseföretag", _i((1560, 1569), (1570, 1572), (1574, 1579), (1660, 1669), (1671, 1672), (1674, 1679)), "B"),
    ("7261", "2.21", "Övriga fordringar", _i((1610, 1619), (1630, 1659), (1680, 1699), 1573, 1673), "B"),
    ("7262", "2.22", "Upparbetad men ej fakturerad intäkt", _i((1620, 1629)), "B"),
    ("7263", "2.23", "Förutbetalda kostnader och upplupna intäkter", _i((1700, 1799)), "B"),
    ("7270", "2.24", "Andelar i koncernföretag", _i((1860, 1869)), "B"),
    ("7271", "2.25", "Övriga kortfristiga placeringar", _i((1800, 1859), (1870, 1899)), "B"),
    ("7281", "2.26", "Kassa, bank och redovisningsmedel", _i((1900, 1999)), "B"),
    ("7301", "2.27", "Bundet eget kapital", _i((2080, 2089)), "B"),
    ("7302", "2.28", "Fritt eget kapital", _i((2090, 2099)), "B"),
    ("7321", "2.29", "Periodiseringsfonder", _i((2110, 2139)), "B"),
    ("7322", "2.30", "Ackumulerade överavskrivningar", _i((2150, 2159)), "B"),
    ("7323", "2.31", "Övriga obeskattade reserver", _i((2160, 2199)), "B"),
    ("7331", "2.32", "Avsättningar för pensioner enligt tryggandelagen", _i((2210, 2219)), "B"),
    ("7332", "2.33", "Övriga avsättningar för pensioner", _i((2230, 2239)), "B"),
    ("7333", "2.34", "Övriga avsättningar", _i((2220, 2229), (2240, 2299)), "B"),
    ("7350", "2.35", "Obligationslån", _i((2310, 2329)), "B"),
    ("7351", "2.36", "Checkräkningskredit (långfristig)", _i((2330, 2339)), "B"),
    ("7352", "2.37", "Övriga skulder till kreditinstitut (långfristiga)", _i((2340, 2359)), "B"),
    ("7353", "2.38", "Skulder till koncern- och intresseföretag (långfristiga)", _i((2360, 2372), (2374, 2379)), "B"),
    ("7354", "2.39", "Övriga långfristiga skulder", _i((2380, 2399), 2373), "B"),
    ("7360", "2.40", "Checkräkningskredit (kortfristig)", _i((2480, 2489)), "B"),
    ("7361", "2.41", "Övriga skulder till kreditinstitut (kortfristiga)", _i((2410, 2419)), "B"),
    ("7362", "2.42", "Förskott från kunder", _i((2420, 2429)), "B"),
    ("7363", "2.43", "Pågående arbeten för annans räkning", _i((2430, 2439)), "B"),
    ("7364", "2.44", "Fakturerad men ej upparbetad intäkt", _i((2450, 2459)), "B"),
    ("7365", "2.45", "Leverantörsskulder", _i((2440, 2449)), "B"),
    ("7366", "2.46", "Växelskulder", _i(2492), "B"),
    ("7367", "2.47", "Skulder till koncern- och intresseföretag (kortfristiga)", _i((2460, 2472), (2474, 2479), (2874, 2879)), "B"),
    ("7369", "2.48", "Övriga skulder", _i((2490, 2491), (2493, 2499), (2600, 2859), (2880, 2899), 2473, 2873), "B"),
    ("7368", "2.49", "Skatteskulder", _i((2500, 2599)), "B"),
    ("7370", "2.50", "Upplupna kostnader och förutbetalda intäkter", _i((2900, 2999)), "B"),
    ("7410", "3.1", "Nettoomsättning", _i((3000, 3799)), "R+"),
    ("7412", "3.3", "Aktiverat arbete för egen räkning", _i((3800, 3899)), "R+"),
    ("7413", "3.4", "Övriga rörelseintäkter", _i((3900, 3999)), "R+"),
    ("7511", "3.5", "Råvaror, förnödenheter och handelsvaror", _i((4000, 4799), (4910, 4920), (4960, 4969), (4980, 4989)), "R-"),
    ("7513", "3.7", "Övriga externa kostnader", _i((5000, 6999)), "R-"),
    ("7514", "3.8", "Personalkostnader", _i((7000, 7699)), "R-"),
    ("7515", "3.9", "Av- och nedskrivningar av materiella och immateriella anläggningstillgångar", _i((7700, 7739), (7750, 7789), (7800, 7899)), "R-"),
    ("7516", "3.10", "Nedskrivningar av omsättningstillgångar utöver normala", _i((7740, 7749), (7790, 7799)), "R-"),
    ("7517", "3.11", "Övriga rörelsekostnader", _i((7900, 7999)), "R-"),
    ("7417", "3.16", "Övriga ränteintäkter och liknande resultatposter", _i((8300, 8369), (8390, 8399)), "R+"),
    ("7522", "3.18", "Räntekostnader och liknande resultatposter", _i((8400, 8499)), "R-"),
    ("7528", "3.25", "Skatt på årets resultat", _i((8900, 8989)), "R-"),
]


def _summa(s, intervall, falt):
    return sum((v[falt] for k, v in s.items() if any(a <= k <= b for a, b in intervall)), NOLL)


def ink2r(ar):
    s = bok.saldon(dt.date(ar, 1, 1), bokslutsdag(ar))
    rader = []
    for kod, punkt, namn, intervall, sida in INK2R:
        if sida == "B":
            v = _summa(s, intervall, "ub")
            belopp = v if kod < "7300" else -v
        else:
            v = _summa(s, intervall, "period")
            belopp = -v if sida == "R+" else v
        rader.append({"kod": kod, "punkt": punkt, "namn": namn, "belopp": int(belopp.quantize(Decimal(1)))})
    res = -sum((v["period"] for k, v in s.items() if 3000 <= k <= 8989), NOLL)
    rader.append({"kod": "7450" if res >= 0 else "7550", "punkt": "3.26" if res >= 0 else "3.27",
                  "namn": "Årets resultat, " + ("vinst" if res >= 0 else "förlust"),
                  "belopp": int(abs(res).quantize(Decimal(1)))})
    tillg = sum(r["belopp"] for r in rader if "7200" <= r["kod"] < "7300")
    ek_skuld = sum(r["belopp"] for r in rader if "7300" <= r["kod"] < "7400")
    return {"rader": rader, "tillgangar": tillg, "ek_skulder": ek_skuld, "resultat": res}


def ink2s(ar):
    b = skatteberakning(ar)
    res, _ = resultat(ar, till_och_med_konto=8989)
    return [
        ("4.1", "Årets resultat, vinst" if res >= 0 else "Årets resultat, förlust (4.2)", int(abs(res))),
        ("4.3a", "Skatt på årets resultat (bokförd)", int(b["bokford_skatt"])),
        ("4.3c", "Andra bokförda kostnader som inte ska dras av", int(b["summa_ej_avdragsgilla"])),
        ("4.15", "Överskott (flyttas till sid 1 ruta 1.1)" if b["overskott"] >= 0 else "Underskott (4.16)",
         int(abs(b["overskott"]))),
    ]


# ------------------------------------------------------------------ checklista

def checklista(ar):
    from verksamhet.models import Faktura

    foretag = Foretag.hamta()
    slut = bokslutsdag(ar)
    s = bok.saldon(dt.date(ar, 1, 1), slut)
    punkter = []

    def punkt(status, rubrik, text="", url=None, query=""):
        punkter.append({"status": status, "rubrik": rubrik, "text": text, "url": url, "query": query})

    ohanterade = Bankpost.objects.filter(datum__year=ar, verifikation__isnull=True, ignorerad=False).count()
    punkt("ok" if not ohanterade else "fel", "Bankhändelser avstämda",
          f"{ohanterade} bankhändelser för {ar} är inte hanterade." if ohanterade else "Alla bankhändelser är hanterade.",
          "bank")
    bank = Bankpost.objects.filter(konto_id=1930, datum__lte=slut, saldo__isnull=False).order_by("-datum", "-id").first()
    bokfort = s.get(1930, {}).get("ub", NOLL)
    if bank and bank.datum.year == ar:
        diff = bokfort - bank.saldo
        punkt("ok" if not diff else "fel", "Företagskontot (1930) stämmer med banken",
              f"Bokfört {kr(bokfort)} kr, banken {kr(bank.saldo)} kr per {bank.datum}."
              + (f" Differens {kr(diff)} kr." if diff else ""), "huvudbok",
              "?konto=1930")
    else:
        punkt("info", "Företagskontot (1930) stämmer med banken",
              f"Jämför bokfört saldo {kr(bokfort)} kr med kontoutdraget per {slut}.", "huvudbok",
              "?konto=1930")
    from planering.frister import momsperioder
    avraknade = [p for p in momsperioder(ar, foretag.momsperiod)
                 if Verifikation.objects.filter(serie="M", text__contains=f"{p[0]}–{p[1]}").exists()]
    antal = len(momsperioder(ar, foretag.momsperiod))
    punkt("ok" if len(avraknade) == antal else "info", "Momsen avräknad för alla perioder",
          f"{len(avraknade)} av {antal} perioder är avräknade här (kan vara gjort i Visma).", "moms", f"?ar={ar}")
    obetalda = Faktura.objects.filter(status=Faktura.SKICKAD, fakturadatum__lte=slut).count()
    punkt("info", "Kundfordringar", f"{obetalda} kundfakturor är obetalda. Bedöm om någon är osäker och ska skrivas ned.",
          "faktura_lista")
    uppl = upparbetat_ej_fakturerat(ar)
    finns = _bokslutsver(ar, "Upplupen intäkt")
    punkt("ok" if finns or not uppl else "varning", "Upparbetat men inte fakturerat",
          (f"Bokfört i {finns.vernr}." if finns else
           f"{kr(uppl)} kr i tid till och med {slut} var inte fakturerad vid årsskiftet. "
           "Bokför som upplupen intäkt." if uppl else "Ingen ofakturerad tid vid årsskiftet."),
          "bokslut", f"?ar={ar}#periodisering")
    avskr = sum((r["belopp"] for r in avskrivningar(ar)), NOLL)
    finns = _bokslutsver(ar, "Avskrivningar")
    punkt("ok" if finns or not avskr else "varning", "Avskrivningar",
          f"Bokförda i {finns.vernr}." if finns else (f"{kr(avskr)} kr att bokföra." if avskr
                                                     else "Inga inventarier att skriva av."),
          "bokslut", f"?ar={ar}#avskrivningar")
    punkt("info", "Förutbetalda och upplupna kostnader",
          "Gå igenom fakturor som avser båda åren (hyra, försäkringar, licenser) och periodisera större belopp.",
          "bokslut", f"?ar={ar}#periodisering")
    finns = _bokslutsver(ar, "Skatt på årets resultat")
    punkt("ok" if finns else "varning", "Skatt på årets resultat",
          f"Bokförd i {finns.vernr}." if finns else "Beräkna och bokför bolagsskatten.", "bokslut", f"?ar={ar}#skatt")
    finns = _bokslutsver(ar, "Årets resultat")
    punkt("ok" if finns else "varning", "Årets resultat bokfört mot eget kapital",
          f"Bokfört i {finns.vernr}." if finns else "Sista steget: för årets resultat till 2099.", "bokslut",
          f"?ar={ar}#resultat")
    last = foretag.last_tom and foretag.last_tom >= slut
    punkt("ok" if last else "varning", "Bokföringen låst",
          f"Låst t.o.m. {foretag.last_tom}." if last else f"Lås bokföringen t.o.m. {slut} när bokslutet är klart.",
          "foretag")
    punkt("info", "Årsarkiv", "Skapa årsarkivet när allt ovan är klart.", "arsarkiv")
    punkt("info", "Årsredovisning och inkomstdeklaration",
          "Exportera SIE (årsarkivet) till ditt årsredovisningsprogram. Använd INK2-underlaget nedan för att "
          "stämma av deklarationen. Årsredovisningen ska vara hos Bolagsverket senast 31 juli och "
          "inkomstdeklarationen hos Skatteverket senast 1 augusti.", "frister")
    return punkter
