"""Import av kund- och leverantörsfakturor från Visma (CSV eller Excel).

Kolumnerna känns igen på rubrikerna (svenska eller engelska) och kan justeras i ett mappningssteg.
Importerade fakturor markeras 'från Visma' och bokförs inte här, eftersom bokföringen redan finns i Visma
(och kommer hit via SIE-importen). Status, förfallodatum och OCR används för reskontra, bankavstämning
och likviditetsprognos.
"""

import csv
import datetime as dt
import io
import json
import re
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Max

FALT = {
    "kund": [
        ("nummer", "Fakturanummer", True, ["fakturanummer", "fakturanr", "invoicenumber", "nummer", "number", "faktura"]),
        ("kundnummer", "Kundnummer", False, ["kundnummer", "kundnr", "customernumber", "customerno"]),
        ("namn", "Kundnamn", False, ["kundnamn", "kund", "customername", "customer", "namn", "name"]),
        ("orgnr", "Org.nr", False, ["organisationsnummer", "orgnr", "corporateidentitynumber"]),
        ("fakturadatum", "Fakturadatum", True, ["fakturadatum", "invoicedate", "datum", "date"]),
        ("forfallodatum", "Förfallodatum", False, ["forfallodatum", "duedate", "forfaller"]),
        ("belopp", "Belopp inkl. moms", True, ["beloppinklmoms", "totalbelopp", "totalamount", "total",
                                                "summa", "belopp", "amount", "attbetala"]),
        ("moms", "Moms", False, ["moms", "momsbelopp", "vat", "vatamount", "totalvatamount"]),
        ("netto", "Belopp exkl. moms", False, ["beloppexklmoms", "netto", "nettobelopp", "amountexclvat",
                                               "totalamountexclvat", "net"]),
        ("rest", "Restbelopp / kvar att betala", False, ["restbelopp", "kvar", "kvarattbetala", "obetalt",
                                                          "remainingamount", "balance", "saldo"]),
        ("status", "Status", False, ["status", "betalstatus"]),
        ("betaldatum", "Betaldatum", False, ["betaldatum", "betalddatum", "paymentdate", "paiddate", "betald"]),
        ("ocr", "OCR", False, ["ocr", "ocrnummer", "ocrnumber", "referens"]),
        ("er_referens", "Er referens", False, ["erreferens", "kundreferens", "yourreference"]),
    ],
    "lev": [
        ("fakturanummer", "Fakturanummer", False, ["fakturanummer", "fakturanr", "invoicenumber", "nummer"]),
        ("levnummer", "Leverantörsnummer", False, ["leverantorsnummer", "levnr", "suppliernumber"]),
        ("namn", "Leverantör", True, ["leverantorsnamn", "leverantor", "suppliername", "supplier", "namn", "name"]),
        ("orgnr", "Org.nr", False, ["organisationsnummer", "orgnr", "corporateidentitynumber"]),
        ("fakturadatum", "Fakturadatum", True, ["fakturadatum", "invoicedate", "datum", "date"]),
        ("forfallodatum", "Förfallodatum", False, ["forfallodatum", "duedate", "forfaller"]),
        ("belopp", "Belopp inkl. moms", True, ["beloppinklmoms", "totalbelopp", "totalamount", "total", "summa",
                                                "belopp", "amount", "attbetala"]),
        ("moms", "Moms", False, ["moms", "momsbelopp", "vat", "vatamount", "totalvatamount"]),
        ("rest", "Restbelopp / kvar att betala", False, ["restbelopp", "kvar", "kvarattbetala", "obetalt",
                                                          "remainingamount", "balance", "saldo"]),
        ("status", "Status", False, ["status", "betalstatus"]),
        ("betaldatum", "Betaldatum", False, ["betaldatum", "paymentdate", "paiddate", "betald"]),
        ("ocr", "OCR", False, ["ocr", "ocrnummer", "ocrnumber", "referens"]),
        ("konto", "Kostnadskonto", False, ["konto", "kostnadskonto", "account"]),
        ("beskrivning", "Beskrivning", False, ["beskrivning", "text", "meddelande", "description"]),
    ],
}

BETALD_ORD = ("betald", "slutbetald", "paid", "fullypaid", "avslutad", "closed")
MAKULERAD_ORD = ("makulerad", "cancelled", "canceled", "krediterad", "voided")


class ImportFel(Exception):
    pass


def _norm(t):
    t = (t or "").strip().lower()
    for a, b in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e")):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", t)


# ------------------------------------------------------------------ läsa fil

def las_fil(data: bytes, filnamn: str):
    """Returnerar (rubriker, rader) där rader är listor av strängar."""
    if filnamn.lower().endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        alla = []
        for row in ws.iter_rows(values_only=True):
            alla.append(["" if v is None else (v.isoformat()[:10] if isinstance(v, (dt.date, dt.datetime))
                                              else str(v)) for v in row])
    else:
        for kod in ("utf-8-sig", "cp1252"):
            try:
                text = data.decode(kod)
                break
            except UnicodeDecodeError:
                continue
        forsta = "\n".join(text.splitlines()[:5])
        sep = max([";", "\t", ","], key=forsta.count)
        alla = list(csv.reader(io.StringIO(text), delimiter=sep))
    alla = [r for r in alla if any((c or "").strip() for c in r)]
    if len(alla) < 2:
        raise ImportFel("Filen innehåller inga rader.")
    # rubrikraden: första raden med minst tre textceller
    for i, r in enumerate(alla[:15]):
        if sum(1 for c in r if c and not _tal(c, tyst=True)) >= 3:
            return [c.strip() for c in r], alla[i + 1:]
    return [c.strip() for c in alla[0]], alla[1:]


def gissa_mappning(typ, rubriker):
    norm = [_norm(r) for r in rubriker]
    mappning, upptagna = {}, set()
    for nyckel, _, _, synonymer in FALT[typ]:
        for syn in synonymer:  # exakt träff först, sedan delträff
            if syn in norm and norm.index(syn) not in upptagna:
                mappning[nyckel] = norm.index(syn)
                break
        else:
            for syn in synonymer:
                hit = next((i for i, n in enumerate(norm) if syn in n and i not in upptagna), None)
                if hit is not None:
                    mappning[nyckel] = hit
                    break
        if nyckel in mappning:
            upptagna.add(mappning[nyckel])
    return mappning


def spara_tillfalligt(typ, rubriker, rader, sie_data=None):
    import base64
    mapp = settings.DATA_DIR / "tmp"
    mapp.mkdir(exist_ok=True)
    nyckel = uuid.uuid4().hex
    (mapp / f"{nyckel}.json").write_text(json.dumps({
        "typ": typ, "rubriker": rubriker, "rader": rader,
        "sie": base64.b64encode(sie_data).decode() if sie_data else None}))
    return nyckel


def hamta_tillfalligt(nyckel):
    if not re.fullmatch(r"[0-9a-f]{32}", nyckel or ""):
        raise ImportFel("Ogiltig import.")
    fil = settings.DATA_DIR / "tmp" / f"{nyckel}.json"
    if not fil.exists():
        raise ImportFel("Importen har gått ut, ladda upp filen igen.")
    return json.loads(fil.read_text()), fil


# ------------------------------------------------------------------ tolkning

def _tal(v, tyst=False):
    s = str(v or "").strip().replace(" ", "").replace(" ", "").replace("kr", "").replace("SEK", "")
    s = s.replace("−", "-")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        if tyst:
            return None
        raise ImportFel(f"Kan inte tolka beloppet '{v}'.")


def _datum(v):
    s = str(v or "").strip()[:10]
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:  # Excel-serienummer
        n = float(s)
        if 20000 < n < 80000:
            return dt.date(1899, 12, 30) + dt.timedelta(days=int(n))
    except ValueError:
        pass
    raise ImportFel(f"Kan inte tolka datumet '{v}'.")


def tolka(typ, rader, mappning):
    """Gör om råa rader till dicts med tolkade värden. Returnerar (poster, fel)."""
    poster, fel = [], []
    for i, rad in enumerate(rader, start=2):
        if any(_norm(c) in ("summa", "total", "totalt", "sum", "totalsumma") for c in rad[:3]):
            continue  # summarad
        hamta = lambda f, rad=rad: (rad[mappning[f]].strip() if f in mappning and mappning[f] < len(rad) else "")  # noqa: E731
        try:
            belopp = _tal(hamta("belopp"))
            if belopp is None:
                continue  # summarader, tomma rader
            moms = _tal(hamta("moms"), tyst=True)
            netto = _tal(hamta("netto"), tyst=True) if typ == "kund" else None
            if moms is None and netto is not None:
                moms = belopp - netto
            rest = _tal(hamta("rest"), tyst=True)
            status = _norm(hamta("status"))
            betaldatum = _datum(hamta("betaldatum")) if hamta("betaldatum") and hamta("betaldatum").lower() not in (
                "ja", "nej", "yes", "no", "true", "false") else None
            makulerad = any(o in status for o in MAKULERAD_ORD)
            if rest is not None:
                betald = rest == 0
            elif status:
                betald = any(status.startswith(o) or status == o for o in BETALD_ORD) and "obetald" not in status \
                    and "ej" not in status and "unpaid" not in status and "delvis" not in status
            else:
                betald = betaldatum is not None or _norm(hamta("betaldatum")) in ("ja", "yes", "true")
            p = {
                "rad": i, "belopp": belopp, "moms": moms or Decimal(0),
                "fakturadatum": _datum(hamta("fakturadatum")),
                "forfallodatum": _datum(hamta("forfallodatum")) if hamta("forfallodatum") else None,
                "betald": betald, "betaldatum": betaldatum, "makulerad": makulerad, "rest": rest,
                "ocr": hamta("ocr"), "namn": hamta("namn"), "orgnr": hamta("orgnr"),
            }
            if not p["fakturadatum"]:
                raise ImportFel("fakturadatum saknas")
            if typ == "kund":
                nummer = re.sub(r"\D", "", hamta("nummer"))
                if not nummer:
                    raise ImportFel("fakturanummer saknas")
                p.update(nummer=int(nummer), kundnummer=hamta("kundnummer"), er_referens=hamta("er_referens"))
                if not (p["kundnummer"] or p["namn"]):
                    raise ImportFel("kund saknas (kundnummer eller kundnamn)")
            else:
                p.update(fakturanummer=hamta("fakturanummer"), levnummer=hamta("levnummer"),
                         konto=hamta("konto"), beskrivning=hamta("beskrivning"))
                if not (p["levnummer"] or p["namn"]):
                    raise ImportFel("leverantör saknas")
            poster.append(p)
        except ImportFel as e:
            fel.append(f"Rad {i}: {e}")
    return poster, fel


# ------------------------------------------------------------------ skapa

def _momssats(netto, moms):
    if not netto or not moms:
        return Decimal(0)
    sats = abs(moms / netto * 100)
    return min([Decimal(25), Decimal(12), Decimal(6), Decimal(0)], key=lambda s: abs(s - sats))


def _hitta_part(modell, nummer, namn, orgnr):
    from django.db.models import Q
    if nummer and nummer.isdigit():
        if obj := modell.objects.filter(nummer=int(nummer)).first():
            return obj, False
    if orgnr:
        siffror = re.sub(r"\D", "", orgnr)
        for o in modell.objects.exclude(orgnr=""):
            if re.sub(r"\D", "", o.orgnr) == siffror:
                return o, False
    if namn:
        if obj := modell.objects.filter(Q(namn__iexact=namn)).first():
            return obj, False
        n = namn.lower()
        traffar = [o for o in modell.objects.all() if o.namn and (o.namn.lower().startswith(n) or n.startswith(o.namn.lower()))]
        if len(traffar) == 1:
            return traffar[0], False
    nytt = (modell.objects.aggregate(m=Max("nummer"))["m"] or 0) + 1
    return modell.objects.create(nummer=int(nummer) if nummer and nummer.isdigit() else nytt,
                                 namn=namn or f"Okänd {nytt}", orgnr=orgnr or ""), True


@transaction.atomic
def importera_kundfakturor(poster):
    from register.models import Foretag, Konto, Kund
    from verksamhet.models import Faktura, Fakturarad

    ny = uppd = nya_kunder = 0
    konto3041 = Konto.objects.filter(pk=3041).first()
    for p in poster:
        kund, skapad = _hitta_part(Kund, p["kundnummer"], p["namn"], p["orgnr"])
        nya_kunder += skapad
        status = Faktura.MAKULERAD if p["makulerad"] else (Faktura.BETALD if p["betald"] else Faktura.SKICKAD)
        befintlig = Faktura.objects.filter(nummer=p["nummer"]).first()
        if befintlig:
            if not befintlig.fran_visma:
                continue  # skapad här – rör inte
            befintlig.status = status
            befintlig.betaldatum = p["betaldatum"] if p["betald"] else None
            befintlig.forfallodatum = p["forfallodatum"] or befintlig.forfallodatum
            befintlig.save()
            uppd += 1
            continue
        forfaller = p["forfallodatum"] or p["fakturadatum"] + dt.timedelta(
            days=kund.betalningsvillkor.dagar if kund.betalningsvillkor else 30)
        f = Faktura(nummer=p["nummer"], kund=kund, status=status, fakturadatum=p["fakturadatum"],
                    forfallodatum=forfaller, betaldatum=p["betaldatum"] if p["betald"] else None,
                    er_referens=p.get("er_referens", ""), ocr=p["ocr"][:30], fran_visma=True,
                    meddelande="Importerad från Visma")
        f.save()
        netto = p["belopp"] - p["moms"]
        Fakturarad.objects.create(faktura=f, beskrivning="Faktura från Visma", antal=1, enhet="st",
                                  a_pris=netto.quantize(Decimal("0.01")), momssats=_momssats(netto, p["moms"]),
                                  konto=konto3041)
        ny += 1
    # Fortsätt nummerserien efter Vismas högsta nummer
    foretag = Foretag.hamta()
    hogsta = Faktura.objects.aggregate(m=Max("nummer"))["m"] or 0
    if foretag.nasta_fakturanummer <= hogsta:
        foretag.nasta_fakturanummer = hogsta + 1
        foretag.save(update_fields=["nasta_fakturanummer"])
    return {"nya": ny, "uppdaterade": uppd, "nya_parter": nya_kunder, "nasta_nummer": foretag.nasta_fakturanummer}


@transaction.atomic
def importera_levfakturor(poster):
    from register.models import Konto, Leverantor

    from .models import Leverantorsfaktura

    ny = uppd = nya_lev = 0
    standard = Konto.objects.filter(pk=6991).first() or Konto.objects.filter(nummer__gte=4000).first()
    for p in poster:
        lev, skapad = _hitta_part(Leverantor, p["levnummer"], p["namn"], p["orgnr"])
        nya_lev += skapad
        nyckel = f"visma:{lev.pk}:{p['fakturanummer'] or p['ocr']}:{p['fakturadatum']}:{p['belopp']}"[:120]
        konto = None
        if p["konto"] and re.sub(r"\D", "", p["konto"]):
            konto = Konto.objects.filter(pk=int(re.sub(r"\D", "", p["konto"])[:4])).first()
        konto = konto or lev.standardkonto or standard
        betaldatum = p["betaldatum"] or (p["forfallodatum"] or p["fakturadatum"] if p["betald"] else None)
        befintlig = Leverantorsfaktura.objects.filter(extern_nyckel=nyckel).first()
        if befintlig:
            if p["betald"] and not befintlig.betaldatum:
                Leverantorsfaktura.objects.filter(pk=befintlig.pk).update(betaldatum=betaldatum)
                uppd += 1
            continue
        if p["makulerad"]:
            continue
        Leverantorsfaktura.objects.create(
            typ=Leverantorsfaktura.FAKTURA, leverantor=lev, fakturanummer=p["fakturanummer"][:100],
            ocr=p["ocr"][:50], fakturadatum=p["fakturadatum"],
            forfallodatum=p["forfallodatum"] or p["fakturadatum"] + dt.timedelta(
                days=lev.betalningsvillkor.dagar if lev.betalningsvillkor else 30),
            beskrivning=(p["beskrivning"] or "Importerad från Visma")[:300], belopp=p["belopp"],
            moms=p["moms"], konto=konto, betaldatum=betaldatum, fran_visma=True, extern_nyckel=nyckel)
        ny += 1
    return {"nya": ny, "uppdaterade": uppd, "nya_parter": nya_lev}


# ------------------------------------------------------------------ från SIE-fil

NUMMER_RE = re.compile(r"(?:faktura|fakt\.?|fa|nr|inv(?:oice)?|#)\s*[:#.\-]?\s*(\d{1,10})", re.I)
BORT_ORD = re.compile(r"\b(kund|leverantörs|lev)?fakturan?\b|\bkreditfaktura\b|\binbetalning\b|\bbetalning\b|"
                      r"\bnr\b|\bfaktnr\b|\bfakt\b|\bverifikation\b|\(visma [a-z]\d+\)|\b\d+\b", re.I)


TOM_SIE = ("SIE-filen innehåller inga verifikationer (#VER), bara företagsuppgifter. Visma exporterade alltså inga "
           "fakturor eller betalningar för den valda perioden. Gör en ny export från Visma med en period som täcker "
           "fakturorna, gärna 'SIE – export av bokföringsdata' för hela räkenskapsåret.")


def ar_sie(data: bytes):
    huvud = data[:4000]
    return huvud.lstrip().startswith(b"#") and (b"#VER" in data or b"#SIETYP" in huvud or b"#FLAGGA" in huvud)


VISMA_RE = re.compile(
    r"^\s*(?:kundfaktura till|kreditfaktura till|inbetalning från|återbetalning till|leverantörsfaktura från|"
    r"leverantörsfaktura|betalning till|utbetalning till)\s+(?P<part>\d+)\s+(?P<namn>.+?),\s*(?P<nr>[^,]+?)\s*$",
    re.I)


def visma_text(text):
    """Tolkar Vismas standardtext 'Kundfaktura till 4 Secure State Cyber AB, 123'.

    Returnerar (kund-/leverantörsnummer, namn, fakturanummer) eller None.
    """
    if m := VISMA_RE.match(text or ""):
        return m.group("part"), m.group("namn").strip(), m.group("nr").strip()
    return None


def _nummer_i(text):
    if v := visma_text(text):
        return v[2]
    if m := NUMMER_RE.search(text or ""):
        return m.group(1)
    if m := re.search(r"\b(\d{1,10})\b", text or ""):
        return m.group(1)
    return ""


def _namn_i(text):
    if v := visma_text(text):
        return v[1]
    t = BORT_ORD.sub(" ", text or "")
    t = re.sub(r"^\s*(till|från)\s+", "", t.strip(), flags=re.I)
    t = re.sub(r"[,;:/\-–#]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _summa(rader, fran, till):
    return sum((b for k, b, _ in rader if fran <= k <= till), Decimal(0))


def fran_sie(data: bytes, typ):
    """Plockar ut kund- eller leverantörsfakturor och deras betalningar ur verifikationerna i en SIE-fil."""
    from . import sie

    vers, _ = sie.las_verifikationer(data)
    poster, fel, betalningar = [], [], []
    for v in vers:
        if sie.VAR_MARKERING.search(v["text"]):
            continue  # skapad i det här systemet och exporterad till Visma – finns redan här
        bank = _summa(v["rader"], 1900, 1999) + _summa(v["rader"], 1630, 1630)
        if typ == "kund":
            fordran = _summa(v["rader"], 1510, 1519)
            if fordran == 0:
                continue
            if fordran > 0 or (fordran < 0 and bank == 0 and _summa(v["rader"], 3000, 3999) > 0):
                # faktura (eller kreditfaktura om intäkten debiteras)
                moms = -_summa(v["rader"], 2610, 2639)
                nummer = re.sub(r"\D", "", _nummer_i(v["text"]))
                if not nummer:
                    fel.append(f"{v['datum']} '{v['text']}': hittar inget fakturanummer")
                    continue
                vt = visma_text(v["text"])
                poster.append({
                    "rad": f"{v['serie']}{v['nummer']}", "belopp": fordran, "moms": moms,
                    "fakturadatum": v["datum"], "forfallodatum": None, "betald": bank != 0,
                    "betaldatum": v["datum"] if bank else None, "makulerad": False, "rest": None, "ocr": "",
                    "namn": _namn_i(v["text"]), "orgnr": "", "nummer": int(nummer),
                    "kundnummer": vt[0] if vt else "",
                    "er_referens": "",
                })
            elif fordran < 0 and bank > 0:
                betalningar.append({"datum": v["datum"], "belopp": -fordran, "nummer": _nummer_i(v["text"]),
                                    "text": v["text"]})
        else:
            skuld = _summa(v["rader"], 2440, 2449)
            if skuld == 0:
                continue
            if skuld < 0 and bank == 0:
                moms = _summa(v["rader"], 2640, 2649)
                kostnad = [(k, b) for k, b, _ in v["rader"] if b > 0 and 4000 <= k <= 8999]
                konto = str(max(kostnad, key=lambda x: x[1])[0]) if kostnad else ""
                ref = _nummer_i(v["text"])
                from .betalfil import _ocr_giltig
                poster.append({
                    "rad": f"{v['serie']}{v['nummer']}", "belopp": -skuld, "moms": moms,
                    "fakturadatum": v["datum"], "forfallodatum": None, "betald": False, "betaldatum": None,
                    "makulerad": False, "rest": None, "ocr": ref if ref.isdigit() and _ocr_giltig(ref) else "", "namn": _namn_i(v["text"]) or "Okänd leverantör",
                    "orgnr": "", "fakturanummer": _nummer_i(v["text"]),
                    "levnummer": (visma_text(v["text"]) or ("",))[0], "konto": konto,
                    "beskrivning": v["text"][:300],
                })
            elif skuld > 0 and bank < 0:
                betalningar.append({"datum": v["datum"], "belopp": skuld, "nummer": _nummer_i(v["text"]),
                                    "namn": _namn_i(v["text"]).lower(), "text": v["text"]})

    # Koppla betalningar till fakturor: först på nummer (och belopp), sedan på unikt belopp (och namn)
    obetalda = [p for p in poster if not p["betald"]]
    for b in betalningar:
        kandidater = [p for p in obetalda if typ == "kund" and b["nummer"] and str(p["nummer"]) == b["nummer"]]
        if typ == "lev" and b["nummer"]:
            kandidater = [p for p in obetalda if p["fakturanummer"] == b["nummer"] and p["belopp"] == b["belopp"]]
        if not kandidater:
            kandidater = [p for p in obetalda if p["belopp"] == b["belopp"] and p["fakturadatum"] <= b["datum"]]
            if typ == "lev" and len(kandidater) > 1 and b.get("namn"):
                kandidater = [p for p in kandidater if p["namn"].lower() in b["namn"] or b["namn"] in p["namn"].lower()]
        if len(kandidater) >= 1 and (len(kandidater) == 1 or typ == "kund"):
            p = kandidater[0]
            p["betald"], p["betaldatum"] = True, b["datum"]
            obetalda.remove(p)
    return poster, fel, len(betalningar)


# ------------------------------------------------------------------ betalningar från SIE

def betalningar_fran_sie(data: bytes):
    """Hittar in- och utbetalningar i en SIE-fil och kopplar dem till öppna fakturor här.

    Inbetalning: 1510 kredit mot bank/skattekonto. Utbetalning: 2440 debet mot bank.
    Returnerar en lista med dict(typ, datum, belopp, text, faktura, hur).
    """
    from verksamhet.models import Faktura

    from . import sie
    from .models import Leverantorsfaktura

    vers, _ = sie.las_verifikationer(data)
    kund_oppna = [f for f in Faktura.objects.filter(status=Faktura.SKICKAD).select_related("kund").prefetch_related(
        "rader")]
    kund_belopp = {f.pk: f.summering()["att_betala"] for f in kund_oppna}
    lev_oppna = list(Leverantorsfaktura.objects.filter(typ="faktura", betaldatum__isnull=True).select_related(
        "leverantor"))
    resultat = []
    for v in vers:
        if sie.VAR_MARKERING.search(v["text"]):
            continue  # skapad här
        bank = _summa(v["rader"], 1900, 1999) + _summa(v["rader"], 1630, 1630)
        fordran, skuld = _summa(v["rader"], 1510, 1519), _summa(v["rader"], 2440, 2449)
        nummer, namn = _nummer_i(v["text"]), _namn_i(v["text"]).lower()
        if fordran < 0 and bank > 0:
            belopp = -fordran
            traff, hur = None, ""
            if nummer:
                traff = next((f for f in kund_oppna if str(f.nummer) == nummer), None)
                hur = "fakturanummer"
            if not traff:
                kand = [f for f in kund_oppna if kund_belopp[f.pk] == belopp and f.fakturadatum <= v["datum"]]
                if len(kand) > 1 and namn:
                    kand = [f for f in kand if f.kund.namn.lower() in namn or namn in f.kund.namn.lower()]
                traff, hur = (kand[0], "belopp") if len(kand) == 1 else (None, "")
            if traff:
                kund_oppna.remove(traff)
                if kund_belopp[traff.pk] != belopp:
                    hur += f" (delbetalning/avvikande belopp, fakturan {kund_belopp[traff.pk]:.2f})"
            resultat.append({"typ": "kund", "datum": v["datum"], "belopp": belopp, "text": v["text"],
                             "faktura": traff, "hur": hur, "ver": f"{v['serie']}{v['nummer']}"})
        elif skuld > 0 and bank < 0:
            belopp = skuld
            kand = []
            if nummer:
                kand = [lf for lf in lev_oppna if nummer in (lf.fakturanummer, lf.ocr) and lf.belopp == belopp]
            hur = "fakturanummer/OCR" if kand else ""
            if not kand:
                kand = [lf for lf in lev_oppna if lf.belopp == belopp and lf.fakturadatum <= v["datum"]]
                if len(kand) > 1 and namn:
                    kand = [lf for lf in kand if lf.motpartsnamn.lower() in namn or namn in lf.motpartsnamn.lower()]
                hur = "belopp" if len(kand) == 1 else ""
            traff = kand[0] if len(kand) == 1 else None
            if traff:
                lev_oppna.remove(traff)
            resultat.append({"typ": "lev", "datum": v["datum"], "belopp": belopp, "text": v["text"],
                             "faktura": traff, "hur": hur, "ver": f"{v['serie']}{v['nummer']}"})
    return resultat


@transaction.atomic
def registrera_betalningar(resultat):
    """Markerar matchade fakturor som betalda. Ingen bokföring skapas – betalningen är bokförd i Visma."""
    from verksamhet.models import Faktura

    from .models import Leverantorsfaktura

    kund = lev = 0
    for r in resultat:
        f = r["faktura"]
        if not f:
            continue
        if r["typ"] == "kund":
            Faktura.objects.filter(pk=f.pk, status=Faktura.SKICKAD).update(status=Faktura.BETALD,
                                                                           betaldatum=r["datum"])
            kund += 1
        else:
            Leverantorsfaktura.objects.filter(pk=f.pk, betaldatum__isnull=True).update(betaldatum=r["datum"])
            lev += 1
    return kund, lev


@transaction.atomic
def rensa_visma(typ):
    """Tar bort fakturor som importerats från Visma (de har ingen bokföring här)."""
    if typ == "kund":
        from verksamhet.models import Faktura, Tidpost
        qs = Faktura.objects.filter(fran_visma=True, verifikationer__isnull=True)
        Tidpost.objects.filter(fakturarad__faktura__in=qs).update(fakturarad=None)
    else:
        from .models import Leverantorsfaktura
        qs = Leverantorsfaktura.objects.filter(fran_visma=True, verifikationer__isnull=True)
    antal = qs.count()
    for obj in qs:
        obj.delete()
    # Dubbletter som skapades av en tidigare felaktig tolkning ("till eWork Group AB" m.fl.)
    if typ == "kund":
        from register.models import Kund
        modell, relationer = Kund, ("fakturor", "uppdrag")
    else:
        from register.models import Leverantor
        modell, relationer = Leverantor, ("fakturor",)
    for p in modell.objects.filter(namn__regex=r"^(till|från|fran) "):
        if not any(getattr(p, r).exists() for r in relationer):
            p.delete()
    return antal
