"""Automatisk avstämning av bankposter.

Ordning (första säkra träffen vinner):
1. Utbetalning med end-to-end-id 'LF<id>' från betalfilen  -> leverantörsfakturan betalas.
2. Inbetalning med belopp + fakturanummer/OCR i texten     -> kundfakturan betalas.
3. Utbetalning med belopp + OCR/fakturanummer              -> leverantörsfakturan betalas.
4. Exakt en redan bokförd verifikation på bankkontot med samma belopp inom ±3 dagar -> kopplas.
5. Matchningsregel markerad 'bokför automatiskt'           -> bokförs mot regelns konto.
Allt annat lämnas till dig, med förslag.
"""

import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import F

from . import tjanster as bok
from .models import Bankpost, Leverantorsfaktura, Matchningsregel, Transaktion, Verifikation


def _siffror(v):
    return "".join(c for c in (v or "") if c.isdigit())


def _innehaller_nummer(post, nummer):
    if not nummer:
        return False
    nummer = str(nummer).strip()
    hay = f"{post.text} {post.referens} {post.motpart}"
    if re.search(rf"(?<![0-9A-Za-z]){re.escape(nummer)}(?![0-9A-Za-z])", hay):
        return True
    return bool(post.referens) and _siffror(post.referens) == _siffror(nummer) and _siffror(nummer) != ""


def kundfaktura_for(post, sakert=True):
    from verksamhet.models import Faktura

    if post.belopp <= 0:
        return None
    kandidater = [f for f in Faktura.objects.filter(status=Faktura.SKICKAD).select_related("kund").prefetch_related(
        "rader") if f.summering()["att_betala"] == post.belopp]
    med_nummer = [f for f in kandidater if _innehaller_nummer(post, f.nummer)]
    if len(med_nummer) == 1:
        return med_nummer[0]
    if not sakert and len(kandidater) == 1:
        return kandidater[0]
    return None


def levfaktura_for(post):
    if post.belopp >= 0:
        return None
    m = re.fullmatch(r"LF(\d+)", post.end_to_end or "")
    if m:
        lf = Leverantorsfaktura.objects.filter(pk=int(m.group(1)), betaldatum__isnull=True).first()
        if lf and lf.belopp == -post.belopp:
            return lf
    kandidater = Leverantorsfaktura.objects.filter(betaldatum__isnull=True, typ=Leverantorsfaktura.FAKTURA,
                                                   belopp=-post.belopp)
    sakra = [lf for lf in kandidater if (lf.ocr and _innehaller_nummer(post, lf.ocr)) or
             (lf.fakturanummer and _innehaller_nummer(post, lf.fakturanummer))]
    return sakra[0] if len(sakra) == 1 else None


def befintlig_ver(post):
    kandidater = list(Transaktion.objects.filter(
        konto=post.konto_id, belopp=post.belopp,
        verifikation__datum__range=(post.datum - timedelta(days=3), post.datum + timedelta(days=3)),
        verifikation__rattar__isnull=True, verifikation__rattelser__isnull=True,
    ).exclude(verifikation__bankposter__isnull=False).values_list("verifikation_id", flat=True).distinct())
    return Verifikation.objects.get(pk=kandidater[0]) if len(kandidater) == 1 else None


def regler_for(post, bara_auto=None):
    qs = Matchningsregel.objects.filter(aktiv=True).select_related("motkonto")
    if bara_auto is not None:
        qs = qs.filter(automatisk=bara_auto)
    return [r for r in qs if r.passar(post)]


def bokfor_regel(post, regel, anvandare=""):
    belopp = -post.belopp  # motkontots sida
    rader = [(post.konto_id, post.belopp)]
    if regel.momssats:
        moms = (belopp * regel.momssats / (100 + regel.momssats)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        # utbetalning: ingående moms; inbetalning: utgående moms enligt satsen
        momskonto = bok.KONTO_INGAENDE_MOMS if belopp > 0 else \
            bok.MOMSKONTO_UTGAENDE.get(Decimal(regel.momssats).normalize(), 2611)
        rader += [(regel.motkonto_id, belopp - moms), (momskonto, moms)]
    else:
        rader.append((regel.motkonto_id, belopp))
    ver = bok.bokfor(post.datum, regel.verifikationstext or post.text, rader, "B", anvandare)
    Matchningsregel.objects.filter(pk=regel.pk).update(traffar=F("traffar") + 1)
    return ver


def matcha(post, anvandare=""):
    """Försöker matcha en post säkert. Returnerar en beskrivning eller None."""
    from verksamhet.models import Faktura

    if post.hanterad:
        return None
    with transaction.atomic():
        if lf := levfaktura_for(post):
            post.verifikation = bok.bokfor_utbetalning(lf, post.datum, post.konto_id, anvandare)
            beskr = f"Leverantörsfaktura {lf.motpartsnamn} betald"
        elif f := kundfaktura_for(post):
            f.status, f.betaldatum = Faktura.BETALD, post.datum
            f.save()
            if not f.fran_visma and not f.verifikationer.filter(serie="F").exists():
                bok.bokfor_kundfaktura(f, anvandare)
            post.verifikation = bok.bokfor_inbetalning(f, post.datum, post.konto_id, anvandare)
            beskr = f"Kundfaktura {f.nummer} betald"
        elif ver := befintlig_ver(post):
            post.verifikation = ver
            beskr = f"Kopplad till {ver.vernr}"
        elif regler := regler_for(post, bara_auto=True):
            post.verifikation = bokfor_regel(post, regler[0], anvandare)
            beskr = f"Regel: {regler[0].namn}"
        else:
            return None
        post.automatisk = True
        post.save()
    return beskr


def hantera(post, atgard, id=None, motkonto=None, text="", spara_regel=False, regeltext="", anvandare=""):
    """Gör en åtgärd på en bankpost, som på bank-sidan. Returnerar posten.

    atgard: kundfaktura, levfaktura, ver (koppla till befintlig), konto (bokför mot motkonto), regel,
    ignorera eller aterstall. Fel ger BokforingsFel.
    """
    from verksamhet.models import Faktura

    if atgard not in ("ignorera", "aterstall") and post.verifikation_id:
        raise bok.BokforingsFel(f"Bankposten är redan hanterad ({post.verifikation.vernr}).")
    with transaction.atomic():
        if atgard == "kundfaktura":
            f = Faktura.objects.filter(pk=id, status=Faktura.SKICKAD).first()
            if not f:
                raise bok.BokforingsFel("Kundfakturan finns inte eller är inte obetald.")
            f.status, f.betaldatum = Faktura.BETALD, post.datum
            f.save()
            if not f.fran_visma and not f.verifikationer.filter(serie="F").exists():
                bok.bokfor_kundfaktura(f, anvandare)
            post.verifikation = bok.bokfor_inbetalning(f, post.datum, post.konto_id, anvandare)
        elif atgard == "levfaktura":
            lf = Leverantorsfaktura.objects.filter(pk=id, betaldatum__isnull=True).first()
            if not lf:
                raise bok.BokforingsFel("Leverantörsfakturan finns inte eller är redan betald.")
            post.verifikation = bok.bokfor_utbetalning(lf, post.datum, post.konto_id, anvandare)
        elif atgard == "ver":
            ver = Verifikation.objects.filter(pk=id).first()
            if not ver:
                raise bok.BokforingsFel("Verifikationen finns inte.")
            post.verifikation = ver
        elif atgard == "konto":
            if motkonto is None:
                raise bok.BokforingsFel("Ange motkonto.")
            motkonto_id = getattr(motkonto, "pk", motkonto)
            post.verifikation = bok.bokfor(post.datum, text or post.text,
                                           [(post.konto_id, post.belopp), (motkonto_id, -post.belopp)], "B", anvandare)
            if spara_regel:
                regeltext = (regeltext or "").strip()
                if not regeltext:
                    raise bok.BokforingsFel("Ange vilken text regeln ska känna igen.")
                Matchningsregel.objects.create(
                    namn=regeltext, text_innehaller=regeltext, riktning="in" if post.belopp > 0 else "ut",
                    motkonto_id=motkonto_id, automatisk=True)
        elif atgard == "regel":
            regel = Matchningsregel.objects.filter(pk=id).first()
            if not regel:
                raise bok.BokforingsFel("Regeln finns inte.")
            post.verifikation = bokfor_regel(post, regel, anvandare)
        elif atgard == "ignorera":
            post.ignorerad = True
        elif atgard == "aterstall":
            post.ignorerad = False
            post.verifikation = None
        else:
            raise bok.BokforingsFel(f"Okänd åtgärd: {atgard}")
        post.save()
    return post


def matcha_alla(anvandare="", poster=None):
    qs = poster if poster is not None else Bankpost.objects.filter(verifikation__isnull=True, ignorerad=False)
    resultat, fel = [], []
    for post in qs.order_by("datum", "id"):
        try:
            if beskr := matcha(post, anvandare):
                resultat.append((post, beskr))
        except bok.BokforingsFel as e:
            fel.append((post, str(e)))
    return resultat, fel


STANDARDREGLER = [("Skattekontot", "skatteverket", "bada", 1630), ("Bankavgift", "avgift", "ut", 6570)]


def skapa_standardregler():
    from register.models import Konto
    for namn, text, riktning, konto in STANDARDREGLER:
        if Konto.objects.filter(pk=konto).exists() and not Matchningsregel.objects.filter(text_innehaller=text).exists():
            Matchningsregel.objects.create(namn=namn, text_innehaller=text, riktning=riktning, motkonto_id=konto,
                                           automatisk=False)
