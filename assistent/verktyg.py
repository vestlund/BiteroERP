"""Det Claude kan göra i Bitero ERP via MCP. Rena funktioner som anropas av assistent/server.py.

Skrivande verktyg (registrera_*, ny_leverantor) bokför direkt. Claude-appen frågar användaren innan
varje anrop, allt loggas i behandlingshistoriken med användaren "Claude", och verifikationer kan bara
rättas, aldrig ändras. Underlagen läses från inkorgen (settings.INKORG_DIR) och flyttas efter bokföring
till inkorgen/Bokförda/ÅÅÅÅ-MM/.
"""

import datetime as dt
import hashlib
import io
import shutil
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from bokforing import tjanster as bok
from bokforing.models import Bilaga, Leverantorsfaktura, Verifikation
from register.models import Foretag, Konto, Leverantor

ANVANDARE = "Claude"
UNDERLAG = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}
BILDER = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
BOKFORDA = "Bokförda"
WEBB = "http://127.0.0.1:8000"


class VerktygsFel(Exception):
    """Fel som ska visas för Claude/användaren som ett vanligt meddelande."""


# ------------------------------------------------------------------ hjälp

def _inkorg():
    mapp = Path(settings.INKORG_DIR)
    mapp.mkdir(parents=True, exist_ok=True)
    return mapp


def _fil(namn):
    """Säker sökväg till en fil i inkorgen (inga ../ eller undermappar)."""
    namn = Path(str(namn)).name
    p = _inkorg() / namn
    if not p.is_file():
        raise VerktygsFel(f"Filen '{namn}' finns inte i inkorgen ({_inkorg()}). Använd verktyget inkorg för att se filerna.")
    if p.suffix.lower() not in UNDERLAG:
        raise VerktygsFel(f"'{namn}' är inte ett underlag (PDF eller bild).")
    return p


def _belopp(v, falt):
    try:
        b = Decimal(str(v).replace(" ", "").replace(" ", "").replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise VerktygsFel(f"Ogiltigt belopp för {falt}: {v}")
    return b


def _datum(v, falt):
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        raise VerktygsFel(f"Ogiltigt datum för {falt}: {v} (använd ÅÅÅÅ-MM-DD)")


def _konto(nr, tillatna=(1000, 8999)):
    try:
        k = Konto.objects.get(pk=int(nr))
    except (Konto.DoesNotExist, ValueError, TypeError):
        raise VerktygsFel(f"Konto {nr} finns inte i kontoplanen. Använd verktyget kontoplan för att hitta rätt konto.")
    if not tillatna[0] <= k.nummer <= tillatna[1]:
        raise VerktygsFel(f"Konto {k} kan inte användas här.")
    return k


def _leverantor(nummer):
    if nummer in (None, "", 0):
        return None
    try:
        return Leverantor.objects.get(nummer=int(nummer))
    except (Leverantor.DoesNotExist, ValueError, TypeError):
        raise VerktygsFel(f"Leverantör nummer {nummer} finns inte. Sök med sok_leverantor eller skapa med ny_leverantor.")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _flytta_till_bokforda(p, datum):
    mal = _inkorg() / BOKFORDA / f"{datum:%Y-%m}"
    mal.mkdir(parents=True, exist_ok=True)
    ny = mal / p.name
    i = 1
    while ny.exists():
        ny = mal / f"{p.stem}_{i}{p.suffix}"
        i += 1
    shutil.move(str(p), str(ny))
    return ny


def _kontrollera_moms(belopp, moms):
    if moms < 0 or belopp <= 0:
        raise VerktygsFel("Belopp ska vara större än noll och moms noll eller mer.")
    if moms >= belopp:
        raise VerktygsFel("Momsen kan inte vara lika stor som eller större än beloppet.")
    netto = belopp - moms
    if moms and netto:
        sats = moms / netto * 100
        if not any(abs(sats - s) < Decimal("1.5") for s in (25, 12, 6)):
            return f"OBS: momsen motsvarar {sats:.1f} %, vilket inte är 25, 12 eller 6 %. Kontrollera."
    return None


# ------------------------------------------------------------------ läsverktyg

def inkorg():
    """Filer som väntar i inkorgen."""
    filer = []
    for p in sorted(_inkorg().iterdir(), key=lambda p: p.stat().st_mtime):
        if p.is_file() and p.suffix.lower() in UNDERLAG:
            data = p.read_bytes()
            redan = Bilaga.objects.filter(sha256=_sha(data)).select_related("levfaktura").first()
            filer.append({"fil": p.name, "storlek_kb": round(len(data) / 1024), "typ": p.suffix.lower().lstrip("."),
                          "andrad": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                          "redan_bokford": str(redan.levfaktura) if redan and redan.levfaktura else None})
    return {"inkorg": str(_inkorg()), "filer": filer}


def visa_underlag(fil):
    """Innehållet i ett underlag: (text, bild_bytes eller None, bildformat).

    PDF: texten och ett tolkningsförslag. Bild: bilden, nedskalad så att den är lätt att läsa.
    """
    p = _fil(fil)
    data = p.read_bytes()
    if p.suffix.lower() == ".pdf":
        from bokforing import kvitto
        try:
            r = kvitto.tolka(data, Leverantor.objects.filter(aktiv=True), Foretag.hamta().orgnr)
        except kvitto.TolkningsFel as e:
            return f"{p.name}: {e}", None, None
        forslag = {k: str(v) for k, v in r.items() if k not in ("text", "hittat", "leverantor") and v not in (None, "")}
        if r.get("leverantor"):
            forslag["leverantor"] = f"{r['leverantor'].nummer} {r['leverantor'].namn}"
        return f"{p.name} (PDF)\nFörslag från avläsningen: {forslag}\n\nText:\n{r['text']}", None, None
    if p.suffix.lower() == ".heic":
        return (f"{p.name} är en HEIC-bild från iPhone som inte kan visas. Spara om den som JPEG "
                "(eller ställ in kameran på 'Mest kompatibel').", None, None)
    from PIL import Image as PILImage
    from PIL import ImageOps
    bild = ImageOps.exif_transpose(PILImage.open(io.BytesIO(data)))
    bild.thumbnail((1600, 1600))
    ut = io.BytesIO()
    bild.convert("RGB").save(ut, "JPEG", quality=85)
    return f"{p.name} (bild, {bild.width}×{bild.height})", ut.getvalue(), "jpeg"


def sok_leverantor(text):
    q = (text or "").strip()
    qs = Leverantor.objects.filter(aktiv=True)
    if q:
        siffror = "".join(c for c in q if c.isdigit())
        filt = Q(namn__icontains=q)
        if siffror:
            filt |= Q(orgnr__contains=siffror[-10:]) | Q(bankgiro__contains=q) | Q(plusgiro__contains=q)
            if siffror.isdigit() and len(siffror) <= 6:
                filt |= Q(nummer=int(siffror))
        qs = qs.filter(filt)
    res = []
    for lev in qs.select_related("standardkonto")[:20]:
        senaste = Leverantorsfaktura.objects.filter(leverantor=lev).order_by("-skapad").first()
        konto = lev.standardkonto or (senaste.konto if senaste else None)
        res.append({"nummer": lev.nummer, "namn": lev.namn, "orgnr": lev.orgnr, "bankgiro": lev.bankgiro,
                    "plusgiro": lev.plusgiro, "foreslaget_konto": str(konto) if konto else None,
                    "moms_procent": str(lev.standard_momssats)})
    return res


def tidigare_kvitton(motpart):
    """Hur liknande kvitton bokförts tidigare (konto och moms), för att föreslå kontering."""
    qs = Leverantorsfaktura.objects.filter(Q(motpart__icontains=motpart) | Q(leverantor__namn__icontains=motpart)) \
        .select_related("konto", "leverantor").order_by("-fakturadatum")[:10]
    return [{"datum": str(x.fakturadatum), "motpart": x.motpartsnamn, "belopp": str(x.belopp), "moms": str(x.moms),
             "konto": str(x.konto), "beskrivning": x.beskrivning} for x in qs]


def kontoplan(sok=""):
    q = (sok or "").strip()
    qs = Konto.objects.all()
    if q.isdigit():
        qs = qs.filter(nummer__startswith=q)
    elif q:
        qs = qs.filter(namn__icontains=q)
    else:
        qs = qs.filter(aktiv=True, nummer__gte=4000, nummer__lt=8000)
    return [{"konto": k.nummer, "namn": k.namn, "aktiv": k.aktiv} for k in qs.order_by("nummer")[:60]]


def obetalda():
    from verksamhet.models import Faktura
    idag = timezone.localdate()
    kund = [{"faktura": f.nummer, "kund": f.kund.namn, "att_betala": str(f.summering()["att_betala"]),
             "forfaller": str(f.forfallodatum), "forfallen": bool(f.forfallodatum and f.forfallodatum < idag)}
            for f in Faktura.objects.filter(status=Faktura.SKICKAD).select_related("kund").prefetch_related("rader")]
    lev = [{"id": lf.pk, "leverantor": lf.motpartsnamn, "fakturanummer": lf.fakturanummer, "belopp": str(lf.belopp),
            "forfaller": str(lf.forfallodatum), "forfallen": bool(lf.forfallen)}
           for lf in Leverantorsfaktura.objects.filter(betaldatum__isnull=True, typ=Leverantorsfaktura.FAKTURA)
           .select_related("leverantor")]
    return {"kundfakturor": kund, "leverantorsfakturor": lev}


def ekonomi():
    """Nyckeltal: saldon, resultat i år och likviditet närmaste veckorna."""
    from planering import likviditet
    idag = timezone.localdate()
    s = bok.saldon(dt.date(idag.year, 1, 1), idag)
    ub = lambda k: str(s.get(k, {}).get("ub", Decimal(0)))  # noqa: E731
    intakter = -sum((v["period"] for k, v in s.items() if 3000 <= k < 4000), Decimal(0))
    kostnader = sum((v["period"] for k, v in s.items() if 4000 <= k < 9000), Decimal(0))
    lp = likviditet.prognos(idag, veckor=8)
    return {"datum": str(idag), "foretagskonto_1930": ub(1930), "skattekonto_1630": ub(1630),
            "intakter_i_ar": str(intakter), "kostnader_i_ar": str(kostnader), "resultat_i_ar": str(intakter - kostnader),
            "likviditet_8_veckor": [{"vecka": v["vecka"], "saldo": str(v["saldo"])} for v in lp["veckor"]],
            "lagsta_saldo": str(lp["lagsta"]["saldo"]) if lp["lagsta"] else None}


def momsrapport(fran, till):
    r = bok.momsrapport(_datum(fran, "fran"), _datum(till, "till"))["rutor"]
    return {ruta: str(b) for ruta, b in sorted(r.items()) if b}


def sok_verifikationer(text, ar=None):
    qs = Verifikation.objects.all()
    if ar:
        qs = qs.filter(ar=int(ar))
    if text:
        qs = qs.filter(Q(text__icontains=text) | Q(transaktioner__text__icontains=text)).distinct()
    return [{"ver": v.vernr, "datum": str(v.datum), "text": v.text, "belopp": str(v.omsattning),
             "lank": f"{WEBB}/bokforing/verifikationer/{v.pk}/"}
            for v in qs.order_by("-datum").prefetch_related("transaktioner")[:25]]


# ------------------------------------------------------------------ skrivverktyg

def _registrera(typ, fil, datum, belopp, moms, konto, leverantor_nummer=None, motpart="", beskrivning="",
                betalkonto=None, fakturanummer="", ocr="", forfallodatum=None, tillat_dubblett=False):
    p = _fil(fil)
    data = p.read_bytes()
    if (redan := Bilaga.objects.filter(sha256=_sha(data)).select_related("levfaktura").first()) and not tillat_dubblett:
        raise VerktygsFel(f"Filen är redan bokförd ({redan.levfaktura or redan.verifikation}). "
                          "Ange tillat_dubblett=true om det verkligen är ett nytt köp.")
    datum, belopp, moms = _datum(datum, "datum"), _belopp(belopp, "belopp"), _belopp(moms or 0, "moms")
    varning = _kontrollera_moms(belopp, moms)
    k = _konto(konto)
    lev = _leverantor(leverantor_nummer)
    if typ == Leverantorsfaktura.FAKTURA and not lev:
        raise VerktygsFel("En leverantörsfaktura kräver leverantör. Använd sok_leverantor eller ny_leverantor.")
    if not lev and not (motpart or "").strip():
        raise VerktygsFel("Ange leverantor_nummer eller motpart (t.ex. butikens namn).")
    lika = Leverantorsfaktura.objects.filter(fakturadatum=datum, belopp=belopp).filter(
        Q(leverantor=lev) if lev else Q(motpart__iexact=(motpart or "").strip()))
    if lika.exists() and not tillat_dubblett:
        raise VerktygsFel(f"Det finns redan en post med samma datum, motpart och belopp ({lika.first()}). "
                          "Ange tillat_dubblett=true om det är ett nytt köp.")
    bkonto = None
    if typ == Leverantorsfaktura.KVITTO:
        bkonto = _konto(betalkonto or 1930, (1900, 2999))
    with transaction.atomic():
        lf = Leverantorsfaktura(
            typ=typ, leverantor=lev, motpart="" if lev else motpart.strip()[:200], fakturanummer=(fakturanummer or "")[:100],
            ocr=(ocr or "")[:50], fakturadatum=datum, beskrivning=(beskrivning or "")[:300], belopp=belopp, moms=moms,
            konto=k, betalkonto=bkonto,
            forfallodatum=_datum(forfallodatum, "forfallodatum") if forfallodatum else (
                datum + dt.timedelta(days=lev.betalningsvillkor.dagar if lev and lev.betalningsvillkor else 30)
                if typ == Leverantorsfaktura.FAKTURA else None))
        lf.full_clean(exclude=["kostnadsstalle"])
        lf.save()
        b = Bilaga(levfaktura=lf, namn=p.name)
        b.fil.save(p.name, ContentFile(data), save=True)
        ver = bok.bokfor_levfaktura(lf, ANVANDARE)
        Bilaga.objects.filter(pk=b.pk).update(verifikation=ver)
        if lev and not lev.standardkonto_id:
            Leverantor.objects.filter(pk=lev.pk).update(standardkonto=k)
    flyttad = _flytta_till_bokforda(p, datum)
    rader = [f"{t.konto_id} {t.konto.namn}: {'debet' if t.belopp > 0 else 'kredit'} {abs(t.belopp)}"
             for t in ver.transaktioner.select_related("konto")]
    return {"verifikation": ver.vernr, "datum": str(ver.datum), "text": ver.text, "kontering": rader,
            "lank": f"{WEBB}/inkop/{lf.pk}/", "underlag_flyttat_till": str(flyttad), "varning": varning}


def registrera_kvitto(fil, datum, belopp, moms, konto, motpart="", leverantor_nummer=None, beskrivning="",
                      betalkonto=1930, tillat_dubblett=False):
    return _registrera(Leverantorsfaktura.KVITTO, fil, datum, belopp, moms, konto, leverantor_nummer, motpart,
                       beskrivning, betalkonto, tillat_dubblett=tillat_dubblett)


def registrera_leverantorsfaktura(fil, leverantor_nummer, fakturadatum, belopp, moms, konto, fakturanummer="",
                                  ocr="", forfallodatum=None, beskrivning="", tillat_dubblett=False):
    return _registrera(Leverantorsfaktura.FAKTURA, fil, fakturadatum, belopp, moms, konto, leverantor_nummer,
                       beskrivning=beskrivning, fakturanummer=fakturanummer, ocr=ocr, forfallodatum=forfallodatum,
                       tillat_dubblett=tillat_dubblett)


def ny_leverantor(namn, orgnr="", bankgiro="", plusgiro="", standardkonto=None, moms_procent=25):
    namn = (namn or "").strip()
    if not namn:
        raise VerktygsFel("Ange leverantörens namn.")
    if orgnr and Leverantor.objects.filter(orgnr=orgnr).exists():
        lev = Leverantor.objects.get(orgnr=orgnr)
        raise VerktygsFel(f"Leverantören finns redan: {lev.nummer} {lev.namn}.")
    from django.db.models import Max
    nummer = (Leverantor.objects.aggregate(m=Max("nummer"))["m"] or 0) + 1
    lev = Leverantor.objects.create(nummer=nummer, namn=namn[:200], orgnr=(orgnr or "")[:20], bankgiro=(bankgiro or "")[:20],
                                    plusgiro=(plusgiro or "")[:20], standard_momssats=Decimal(str(moms_procent)),
                                    standardkonto=_konto(standardkonto) if standardkonto else None)
    return {"nummer": lev.nummer, "namn": lev.namn, "lank": f"{WEBB}/register/leverantorer/{lev.pk}/"}
