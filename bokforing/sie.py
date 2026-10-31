"""SIE 4: export av verifikationer (SIE 4I) och import av ingående balanser."""

import re
import shlex
from decimal import Decimal

from django.utils import timezone

from register.models import Konto

from .models import IngaendeBalans


def _q(text):
    return '"' + str(text).replace('"', "'").replace('\u2013', '-').replace('\u2014', '-') + '"'


def exportera(verifikationer, foretag):
    rader = [
        "#FLAGGA 0", "#FORMAT PC8", "#SIETYP 4",
        f"#PROGRAM {_q('Bitero ERP')} 2.0",
        f"#GEN {timezone.localdate():%Y%m%d}",
        f"#FNAMN {_q(foretag.namn)}",
    ]
    if foretag.orgnr:
        rader.append(f"#ORGNR {foretag.orgnr}")
    vers = list(verifikationer.prefetch_related("transaktioner__konto"))
    konton = {t.konto for v in vers for t in v.transaktioner.all()}
    for k in sorted(konton, key=lambda k: k.nummer):
        rader.append(f"#KONTO {k.nummer} {_q(k.namn)}")
    for v in vers:
        # Serie och nummer lämnas tomma så att Visma numrerar själv; vårt nummer står i texten.
        rader.append(f'#VER "" "" {v.datum:%Y%m%d} {_q(f"{v.text} ({v.vernr})")}')
        rader.append("{")
        for t in v.transaktioner.all():
            rader.append(f"   #TRANS {t.konto_id} {{}} {t.belopp:.2f}" + (f" \"\" {_q(t.text)}" if t.text else ""))
        rader.append("}")
    return ("\r\n".join(rader) + "\r\n").encode("cp437", errors="replace"), len(vers)


def _avkoda(data):
    for kod in ("cp437", "utf-8"):
        try:
            text = data.decode(kod)
            if kod == "cp437" and "#FORMAT PC8" not in text and "Ã" in text:
                continue
            return text
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def importera_ib(data, ar=None):
    """Läser #IB 0 (ingående balans innevarande år) och #RAR 0 ur en SIE-fil (t.ex. från Visma).

    Returnerar (år, antal konton). Befintliga IB för året ersätts.
    """
    text = _avkoda(data)
    rar = re.search(r"^#RAR\s+0\s+(\d{8})", text, re.M)
    ar = ar or (int(rar.group(1)[:4]) if rar else None)
    if not ar:
        raise ValueError("Filen saknar #RAR 0 – ange år.")
    kontonamn, ib = {}, {}
    for rad in text.splitlines():
        rad = rad.strip()
        if rad.startswith("#KONTO"):
            delar = shlex.split(rad, posix=True)
            if len(delar) >= 3 and delar[1].isdigit():
                kontonamn[int(delar[1])] = delar[2]
        elif rad.startswith("#IB"):
            delar = rad.split()
            if len(delar) >= 4 and delar[1] == "0" and delar[2].isdigit():
                ib[int(delar[2])] = Decimal(delar[3])
    IngaendeBalans.objects.filter(ar=ar).delete()
    for nr, belopp in ib.items():
        if belopp == 0:
            continue
        konto, _ = Konto.objects.get_or_create(nummer=nr, defaults={"namn": kontonamn.get(nr, f"Konto {nr}")})
        if not konto.aktiv:
            Konto.objects.filter(pk=nr).update(aktiv=True)
        IngaendeBalans.objects.create(ar=ar, konto=konto, belopp=belopp)
    return ar, len([b for b in ib.values() if b])


# ------------------------------------------------------------------ verifikationer från Visma

VAR_MARKERING = re.compile(r"\(([A-Z])(\d+)\)\s*$")


def _delar(rad):
    """Delar en SIE-rad i fält; {…} hålls ihop som ett fält."""
    rad = rad.replace("{", ' "{').replace("}", '}" ')
    try:
        return shlex.split(rad, posix=True)
    except ValueError:
        return rad.split()


def las_verifikationer(data):
    """Returnerar lista av dict(serie, nummer, datum, text, rader=[(konto, belopp, text)])."""
    import datetime as dt
    text = _avkoda(data)
    vers, aktuell, i_block = [], None, False
    kontonamn = {}
    for rad in text.splitlines():
        r = rad.strip()
        if r.startswith("#KONTO"):
            d = _delar(r)
            if len(d) >= 3 and d[1].isdigit():
                kontonamn[int(d[1])] = d[2]
        elif r.startswith("#VER"):
            d = _delar(r)
            datum = dt.datetime.strptime(d[3], "%Y%m%d").date()
            aktuell = {"serie": d[1], "nummer": d[2], "datum": datum, "text": d[4] if len(d) > 4 else "",
                       "rader": []}
        elif r == "{" and aktuell is not None:
            i_block = True
        elif r == "}" and aktuell is not None:
            vers.append(aktuell)
            aktuell, i_block = None, False
        elif r.startswith("#TRANS") and i_block:
            d = _delar(r)
            # #TRANS konto {objekt} belopp [transdat] [text] ...
            konto = int(d[1])
            belopp = Decimal(d[3])
            ttext = d[5] if len(d) > 5 else ""
            aktuell["rader"].append((konto, belopp, ttext))
        # #RTRANS och #BTRANS ignoreras: #TRANS bär det gällande värdet
    return vers, kontonamn


def importera_verifikationer(data, anvandare=""):
    """Läser in verifikationer från en SIE 4-fil från Visma som serie V.

    Hoppar över verifikationer som redan importerats (samma serie/nummer/datum) och sådana som
    ursprungligen skapats här och exporterats till Visma (känns igen på '(F12)' i texten).
    """
    from django.db import transaction

    from . import tjanster as bok
    from .models import Verifikation

    vers, kontonamn = las_verifikationer(data)
    nya = hoppade_egna = fanns = 0
    with transaction.atomic():
        for v in vers:
            extern = f"{v['serie']}-{v['nummer']}-{v['datum']:%Y%m%d}"
            if Verifikation.objects.filter(extern_id=extern).exists():
                fanns += 1
                continue
            m = VAR_MARKERING.search(v["text"])
            if m and Verifikation.objects.filter(serie=m.group(1), nummer=int(m.group(2)),
                                                 ar=v["datum"].year).exists():
                hoppade_egna += 1
                continue
            for konto, _, _ in v["rader"]:
                Konto.objects.get_or_create(nummer=konto, defaults={"namn": kontonamn.get(konto, f"Konto {konto}"),
                                                                    "aktiv": True})
            rader = [{"konto": k, "belopp": b, "text": t} for k, b, t in v["rader"]]
            ver = bok.bokfor(v["datum"], f"{v['text'] or 'Verifikation'} (Visma {v['serie']}{v['nummer']})", rader,
                             serie="V", anvandare=anvandare)
            Verifikation.objects.filter(pk=ver.pk).update(extern_id=extern, sie_exporterad=ver.skapad)
            nya += 1
    return {"nya": nya, "egna": hoppade_egna, "fanns": fanns, "totalt": len(vers)}
