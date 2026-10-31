"""Bokföringslogik: skapa verifikationer, rättelser, saldon och moms."""

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Sum

from logg.sparning import logga_objekt
from register.models import Foretag, Konto

from .models import Bankpost, IngaendeBalans, Leverantorsfaktura, Transaktion, Verifikation

NOLL = Decimal("0.00")


def _d(v):
    """SQLite summerar decimaltal som flyttal – avrunda tillbaka till ören."""
    return Decimal(str(v or 0)).quantize(NOLL)
MOMSKONTO_UTGAENDE = {Decimal("25"): 2611, Decimal("12"): 2621, Decimal("6"): 2631}
KONTO_KUNDFORDRAN = 1510
KONTO_LEVSKULD = 2440
KONTO_INGAENDE_MOMS = 2641
KONTO_MOMSREDOVISNING = 2650
KONTO_ORES = 3740
KONTO_BANK = 1930


class BokforingsFel(Exception):
    pass


def _konto(nr):
    try:
        k = Konto.objects.get(pk=int(nr))
    except (Konto.DoesNotExist, ValueError, TypeError):
        raise BokforingsFel(f"Konto {nr} finns inte i kontoplanen.")
    if not k.aktiv:  # aktivera automatiskt så att kontot syns i listor
        Konto.objects.filter(pk=k.pk).update(aktiv=True)
    return k


@transaction.atomic
def bokfor(datum, text, rader, serie="A", anvandare="", rattar=None, kundfaktura=None, levfaktura=None,
           lonekorning=None):
    """Skapar en verifikation.

    rader: lista av (konto, belopp) eller dict(konto=, belopp=, text=, kostnadsstalle=).
    Belopp: positivt = debet, negativt = kredit. Summan måste vara 0.
    """
    rader = [r if isinstance(r, dict) else {"konto": r[0], "belopp": r[1]} for r in rader]
    rader = [r for r in rader if Decimal(r["belopp"]) != 0]
    if len(rader) < 2:
        raise BokforingsFel("En verifikation behöver minst två rader med belopp.")
    summa = sum(Decimal(r["belopp"]) for r in rader)
    if summa != 0:
        raise BokforingsFel(f"Verifikationen balanserar inte (differens {summa} kr).")
    if not text.strip():
        raise BokforingsFel("Verifikationen behöver en text.")
    foretag = Foretag.hamta()
    if foretag.last_tom and datum <= foretag.last_tom:
        raise BokforingsFel(f"Bokföringen är låst till och med {foretag.last_tom}.")

    ar = datum.year
    senaste = Verifikation.objects.select_for_update().filter(serie=serie, ar=ar).aggregate(m=Max("nummer"))["m"]
    ver = Verifikation(serie=serie, ar=ar, nummer=(senaste or 0) + 1, datum=datum, text=text.strip()[:300],
                       skapad_av=anvandare, rattar=rattar, kundfaktura=kundfaktura, levfaktura=levfaktura,
                       lonekorning=lonekorning)
    ver.save()
    for r in rader:
        Transaktion.objects.create(
            verifikation=ver, konto=_konto(r["konto"]), belopp=Decimal(r["belopp"]).quantize(Decimal("0.01")),
            text=(r.get("text") or "")[:300], kostnadsstalle=r.get("kostnadsstalle"))
    logga_objekt("bokford", ver, {
        "vernr": ver.vernr, "ar": ver.ar, "datum": ver.datum, "text": ver.text,
        "rattar": ver.rattar.vernr if ver.rattar else None,
        "rader": [[t.konto_id, str(t.belopp), t.text] for t in ver.transaktioner.all()],
    })
    return ver


def rattelse(ver, datum, anvandare="", text=None):
    """Vänder en verifikation (alla belopp med omvänt tecken)."""
    if ver.ar_rattad:
        raise BokforingsFel(f"{ver.vernr} är redan rättad.")
    rader = [{"konto": t.konto_id, "belopp": -t.belopp, "text": t.text, "kostnadsstalle": t.kostnadsstalle}
             for t in ver.transaktioner.all()]
    return bokfor(datum, text or f"Rättelse av {ver.vernr}: {ver.text}", rader, serie=ver.serie,
                  anvandare=anvandare, rattar=ver, kundfaktura=ver.kundfaktura, levfaktura=ver.levfaktura,
                  lonekorning=ver.lonekorning)


# ------------------------------------------------------------- kundfakturor

def kundfaktura_rader(f):
    s = f.summering()
    fordran = Foretag.hamta().kundfordringskonto_id or KONTO_KUNDFORDRAN
    rader = [{"konto": fordran, "belopp": s["att_betala"], "text": f.kund.namn}]
    per_konto = defaultdict(Decimal)
    for r in f.rader.all():
        per_konto[(r.konto_id or 3041, r.kostnadsstalle)] += r.belopp
    for (konto, ks), belopp in per_konto.items():
        rader.append({"konto": konto, "belopp": -belopp, "kostnadsstalle": ks})
    for sats, m in s["moms"].items():
        if m["moms"]:
            rader.append({"konto": MOMSKONTO_UTGAENDE.get(Decimal(sats), 2611), "belopp": -m["moms"]})
    if s["oresavrundning"]:
        rader.append({"konto": KONTO_ORES, "belopp": -s["oresavrundning"]})
    return rader


def bokfor_kundfaktura(f, anvandare=""):
    if f.verifikationer.filter(serie="F", rattar__isnull=True).exists():
        return None
    return bokfor(f.fakturadatum, f"Kundfaktura {f.nummer} {f.kund.namn}", kundfaktura_rader(f),
                  serie="F", anvandare=anvandare, kundfaktura=f)


def bokfor_inbetalning(f, datum, bankkonto=None, anvandare=""):
    belopp = f.summering()["att_betala"]
    fordran = Foretag.hamta().kundfordringskonto_id or KONTO_KUNDFORDRAN
    return bokfor(datum, f"Inbetalning kundfaktura {f.nummer} {f.kund.namn}",
                  [(bankkonto or KONTO_BANK, belopp), (fordran, -belopp)],
                  serie="I", anvandare=anvandare, kundfaktura=f)


def aktiva_ver(qs):
    """Verifikationer som varken är rättelser eller har rättats."""
    return qs.filter(rattar__isnull=True, rattelser__isnull=True)


# ------------------------------------------------------- leverantörsfakturor

def levfaktura_rader(lf):
    rader = [{"konto": lf.konto_id, "belopp": lf.netto, "text": lf.beskrivning, "kostnadsstalle": lf.kostnadsstalle}]
    if lf.moms:
        rader.append({"konto": KONTO_INGAENDE_MOMS, "belopp": lf.moms})
    motkonto = (lf.betalkonto_id or KONTO_BANK) if lf.typ == Leverantorsfaktura.KVITTO else KONTO_LEVSKULD
    rader.append({"konto": motkonto, "belopp": -lf.belopp, "text": lf.motpartsnamn})
    return rader


def bokfor_levfaktura(lf, anvandare=""):
    typtext = "Kvitto" if lf.typ == lf.KVITTO else "Leverantörsfaktura"
    text = f"{typtext} {lf.motpartsnamn}" + (f" {lf.fakturanummer}" if lf.fakturanummer else "") + \
           (f" – {lf.beskrivning}" if lf.beskrivning else "")
    ver = bokfor(lf.fakturadatum, text, levfaktura_rader(lf), serie="L", anvandare=anvandare, levfaktura=lf)
    if lf.typ == lf.KVITTO and not lf.betaldatum:
        Leverantorsfaktura.objects.filter(pk=lf.pk).update(betaldatum=lf.fakturadatum)
        lf.betaldatum = lf.fakturadatum
    return ver


def bokfor_utbetalning(lf, datum, bankkonto=None, anvandare=""):
    ver = bokfor(datum, f"Betalning {lf.motpartsnamn} {lf.fakturanummer}".strip(),
                 [(KONTO_LEVSKULD, lf.belopp), (bankkonto or KONTO_BANK, -lf.belopp)],
                 serie="U", anvandare=anvandare, levfaktura=lf)
    Leverantorsfaktura.objects.filter(pk=lf.pk).update(betaldatum=datum)
    lf.betaldatum = datum
    return ver


# ------------------------------------------------------------------ saldon

def saldon(fran, till, konton=None):
    """Returnerar {kontonr: {"ib": , "period": , "ub": }}.

    IB för balanskonton = ingående balans för året + transaktioner från årets början till dagen före 'fran'.
    Resultatkonton startar på 0 vid årets början.
    """
    arstart = fran.replace(month=1, day=1)
    ib = defaultdict(Decimal)
    for b in IngaendeBalans.objects.filter(ar=fran.year):
        if b.konto_id < 3000:
            ib[b.konto_id] += b.belopp
    fore = Transaktion.objects.filter(verifikation__datum__gte=arstart, verifikation__datum__lt=fran)
    for k, s in fore.values_list("konto").annotate(s=Sum("belopp")):
        ib[k] += _d(s)
    period = defaultdict(Decimal)
    for k, s in Transaktion.objects.filter(verifikation__datum__range=(fran, till)).values_list(
            "konto").annotate(s=Sum("belopp")):
        period[k] += _d(s)
    alla = set(ib) | set(period)
    if konton:
        alla &= set(konton)
    return {k: {"ib": ib[k], "period": period[k], "ub": ib[k] + period[k]} for k in sorted(alla)}


# -------------------------------------------------------------------- moms

MOMSRUTOR = [
    ("A", "Momspliktig försäljning eller uttag exkl. moms", ["05", "06", "07", "08"]),
    ("B", "Utgående moms på försäljning", ["10", "11", "12"]),
    ("C", "Momspliktiga inköp vid omvänd skattskyldighet", ["20", "21", "22", "23", "24"]),
    ("D", "Utgående moms på inköp", ["30", "31", "32"]),
    ("H", "Försäljning m.m. som är undantagen/momsfri", ["35", "36", "37", "38", "39", "40", "41", "42"]),
    ("F", "Ingående moms", ["48"]),
]
RUTNAMN = {
    "05": "Momspliktig försäljning som inte ingår i ruta 06, 07 eller 08",
    "06": "Momspliktiga uttag", "07": "Beskattningsunderlag vid vinstmarginalbeskattning",
    "08": "Hyresinkomster vid frivillig skattskyldighet",
    "10": "Utgående moms 25 %", "11": "Utgående moms 12 %", "12": "Utgående moms 6 %",
    "20": "Inköp av varor från annat EU-land", "21": "Inköp av tjänster från annat EU-land",
    "22": "Inköp av tjänster från land utanför EU", "23": "Inköp av varor i Sverige (omvänd skattskyldighet)",
    "24": "Övriga inköp av tjänster",
    "30": "Utgående moms 25 % (inköp)", "31": "Utgående moms 12 % (inköp)", "32": "Utgående moms 6 % (inköp)",
    "35": "Försäljning av varor till annat EU-land", "36": "Försäljning av varor utanför EU",
    "37": "Mellanmans inköp av varor vid trepartshandel", "38": "Mellanmans försäljning vid trepartshandel",
    "39": "Försäljning av tjänster till annat EU-land", "40": "Övrig försäljning av tjänster omsatta utomlands",
    "41": "Försäljning när köparen är skattskyldig i Sverige", "42": "Övrig försäljning m.m.",
    "48": "Ingående moms att dra av", "49": "Moms att betala eller få tillbaka",
}
KREDITRUTOR = {"05", "06", "07", "08", "10", "11", "12", "30", "31", "32",
               "35", "36", "37", "38", "39", "40", "41", "42"}


def _avrakningar():
    """Verifikationer som för över moms till 2650 (t.ex. Vismas momsavräkning importerad via SIE)."""
    return Transaktion.objects.filter(konto_id=KONTO_MOMSREDOVISNING, verifikation__serie="V").values(
        "verifikation_id")


def momsrapport(fran, till):
    """Summerar per momsruta utifrån kontoplanens momskoder (från Visma)."""
    konton = {k.nummer: k.momskod.split("-")[0] for k in Konto.objects.exclude(momskod="")}
    rutor = defaultdict(Decimal)
    per_konto = defaultdict(lambda: defaultdict(Decimal))
    qs = Transaktion.objects.filter(verifikation__datum__range=(fran, till), konto__in=konton.keys())
    qs = qs.exclude(verifikation__serie="M").exclude(verifikation__in=_avrakningar())  # avräkningar räknas inte
    for k, s in qs.values_list("konto").annotate(s=Sum("belopp")):
        s = _d(s)
        ruta = konton[k]
        belopp = -s if ruta in KREDITRUTOR else s
        rutor[ruta] += belopp
        per_konto[ruta][k] += belopp
    # Underlag (ruta 05 m.fl.) redovisas i hela kronor, momsen i kronor och ören på blanketten:
    att_betala = sum(rutor[r] for r in ["10", "11", "12", "30", "31", "32", "60", "61", "62"]) - rutor["48"]
    rutor["49"] = att_betala
    return {"rutor": dict(rutor), "per_konto": {r: dict(v) for r, v in per_konto.items()}}


def momskonto_saldon(fran, till):
    """Saldon på momskontona (26xx utom 2650) under perioden, exkl. tidigare avräkningar."""
    qs = Transaktion.objects.filter(
        verifikation__datum__range=(fran, till), konto__nummer__gte=2600, konto__nummer__lt=2700,
    ).exclude(konto__nummer=KONTO_MOMSREDOVISNING).exclude(verifikation__serie="M").exclude(
        verifikation__in=_avrakningar())
    return {k: _d(s) for k, s in qs.values_list("konto").annotate(s=Sum("belopp")) if _d(s)}


def bokfor_momsavrakning(fran, till, datum, anvandare=""):
    if Verifikation.objects.filter(serie="M", text__contains=f"{fran}–{till}", rattelser__isnull=True,
                                   rattar__isnull=True).exists():
        raise BokforingsFel("Perioden är redan avräknad.")
    saldo = momskonto_saldon(fran, till)
    if not saldo:
        raise BokforingsFel("Inga momstransaktioner i perioden.")
    rader = [(k, -s) for k, s in saldo.items()]
    rader.append((KONTO_MOMSREDOVISNING, sum(saldo.values())))
    return bokfor(datum, f"Momsredovisning {fran}–{till}", rader, serie="M", anvandare=anvandare)


# ------------------------------------------------------------------- mallar

MALLAR = [
    {"id": "skatt_in", "namn": "Insättning till skattekontot", "rader": [(1630, "+"), (1930, "-")]},
    {"id": "moms_skatt", "namn": "Moms dras från skattekontot", "rader": [(2650, "+"), (1630, "-")]},
    {"id": "moms_tillbaka", "namn": "Momsfordran sätts in på skattekontot", "rader": [(1630, "+"), (2650, "-")]},
    {"id": "fskatt", "namn": "Debiterad preliminärskatt (F-skatt)", "rader": [(2518, "+"), (1630, "-")]},
    {"id": "lonskatt", "namn": "Arbetsgivaravgifter + personalskatt dras", "rader": [(2730, "+"), (2710, "+"), (1630, "-")]},
    {"id": "skatteranta", "namn": "Kostnadsränta skattekontot", "rader": [(8423, "+"), (1630, "-")]},
    {"id": "skatt_ut", "namn": "Utbetalning från skattekontot till bank", "rader": [(1930, "+"), (1630, "-")]},
    {"id": "bankavgift", "namn": "Bankavgift", "rader": [(6570, "+"), (1930, "-")]},
    {"id": "eget_utlagg", "namn": "Återbetalning av eget utlägg", "rader": [(2893, "+"), (1930, "-")]},
]


def bankpost_forslag(post):
    """Föreslår matchningar för en bankpost."""
    from verksamhet.models import Faktura

    forslag = []
    if post.belopp > 0:
        for f in Faktura.objects.filter(status=Faktura.SKICKAD).select_related("kund").prefetch_related("rader"):
            if f.summering()["att_betala"] == post.belopp:
                forslag.append({"typ": "kundfaktura", "obj": f, "text": f"Kundfaktura {f.nummer} {f.kund.namn}"})
    else:
        for lf in Leverantorsfaktura.objects.filter(betaldatum__isnull=True, belopp=-post.belopp,
                                                    typ=Leverantorsfaktura.FAKTURA).select_related("leverantor"):
            forslag.append({"typ": "levfaktura", "obj": lf, "text": f"Leverantörsfaktura {lf}"})
    # Redan bokförda verifikationer på bankkontot med samma belopp och nära datum
    from datetime import timedelta
    kandidater = Transaktion.objects.filter(
        konto=post.konto_id, belopp=post.belopp,
        verifikation__datum__range=(post.datum - timedelta(days=7), post.datum + timedelta(days=7)),
    ).exclude(verifikation__bankposter__isnull=False).exclude(verifikation__rattar__isnull=False).exclude(
        verifikation__rattelser__isnull=False).select_related("verifikation")
    for t in kandidater:
        forslag.append({"typ": "ver", "obj": t.verifikation, "text": f"Befintlig {t.verifikation}"})
    return forslag


def ofakturerade_bankposter():
    return Bankpost.objects.filter(verifikation__isnull=True, ignorerad=False)
