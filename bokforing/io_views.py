"""Sidan Import och export (Inställningar)."""

import csv
import datetime as dt
import io
import json
from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from register import visma_io
from register.models import Foretag

from . import betalfil, sie
from .forms import BetalfilForm, SieInlasningForm, mappningsform
from .models import Leverantorsfaktura

EU = {"AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU",
      "LV", "MT", "NL", "PL", "PT", "RO", "SI", "SK"}

POSTER = [
    ("kundfakturor", "Import av kundfakturor från Visma"),
    ("levfakturor", "Import av leverantörsfakturor från Visma"),
    ("betalningar", "Import av betalningar från Visma (SIE)"),
    ("kunder", "Import och export av kundregister"),
    ("artiklar", "Import och export av artikelregister"),
    ("leverantorer", "Import och export av leverantörsregister"),
    ("oss", "Underlag till Skatteverket för One Stop Shop"),
    ("kostnadsstallen", "Import och export av resultatenheter"),
    ("foretag", "Export av företagsuppgifter"),
    ("betalfil", "Skapa fil till banken med leverantörsfakturor att betala"),
    ("sie-kund", "SIE – export av kundfakturor och inbetalningar"),
    ("sie", "SIE – import och export av bokföringsdata"),
    ("konton", "Import och export av kontoplan"),
]
REGISTER = {"kunder", "artiklar", "leverantorer", "kostnadsstallen", "konton"}


def _fil(innehall, namn, typ="text/csv; charset=utf-8"):
    r = HttpResponse(innehall, content_type=typ)
    r["Content-Disposition"] = f'attachment; filename="{namn}"'
    return r


def lista(request):
    q = request.GET.get("q", "").lower()
    poster = [(k, n) for k, n in POSTER if q in n.lower()]
    return render(request, "bokforing/io_lista.html", {"poster": poster, "q": q})


def post(request, slug):
    namn = dict(POSTER).get(slug)
    if not namn:
        raise Http404
    idag = timezone.localdate()
    ctx = {"slug": slug, "namn": namn}

    if slug in REGISTER:
        if request.method == "POST" and request.FILES.get("fil"):
            try:
                antal = visma_io.importera(slug, request.FILES["fil"].read())
                messages.success(request, f"{antal} rader lästes in. Befintliga poster uppdaterades på nummer.")
            except Exception as e:
                messages.error(request, f"Kunde inte läsa filen: {e}")
            return redirect(request.path)
        if "exportera" in request.GET:
            data, prefix = visma_io.exportera(slug)
            return _fil(data, f"{prefix}_Export_{idag:%Y%m%d}.csv")
        ctx["register"] = visma_io.NAMN[slug]

    elif slug == "betalningar":
        return _betalningsimport(request, ctx)

    elif slug in ("kundfakturor", "levfakturor"):
        return _fakturaimport(request, slug, ctx)

    elif slug == "foretag":
        if "exportera" in request.GET:
            f = Foretag.hamta()
            falt = ["namn", "orgnr", "momsnummer", "adress", "postnummer", "ort", "telefon", "epost", "webb",
                    "bankgiro", "plusgiro", "iban", "bic", "f_skatt", "var_referens", "momsperiod"]
            if request.GET.get("format") == "json":
                return _fil(json.dumps({k: getattr(f, k) for k in falt}, ensure_ascii=False, indent=2),
                            "foretagsuppgifter.json", "application/json")
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
            w.writerow(["Fält", "Värde"])
            for k in falt:
                w.writerow([Foretag._meta.get_field(k).verbose_name, getattr(f, k)])
            return _fil("﻿" + buf.getvalue(), "foretagsuppgifter.csv")

    elif slug == "betalfil":
        obetalda = Leverantorsfaktura.objects.filter(typ="faktura", betaldatum__isnull=True).select_related(
            "leverantor").order_by("forfallodatum")
        if request.method == "POST":
            form = BetalfilForm(request.POST)
            if not form.is_valid():
                messages.error(request, " ".join(e for fel in form.errors.values() for e in fel))
                return redirect(request.path)
            datum = form.cleaned_data["datum"]
            try:
                data, antal, total = betalfil.skapa(list(form.cleaned_data["faktura"].select_related("leverantor")),
                                                    Foretag.hamta(), datum)
                return _fil(data, f"betalningar_{datum:%Y%m%d}.xml", "application/xml")
            except betalfil.BetalfilFel as e:
                messages.error(request, str(e))
                return redirect(request.path)
        ctx.update(obetalda=obetalda, idag=idag, foretag_iban=Foretag.hamta().iban)

    elif slug == "sie-kund":
        ctx.update(fran=idag.replace(month=1, day=1), till=idag)

    elif slug == "sie":
        form = SieInlasningForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data["fil"].read()
            try:
                if form.cleaned_data["vad"] == "ib":
                    ar, antal = sie.importera_ib(data)
                    messages.success(request, f"Ingående balanser för {ar} importerades ({antal} konton).")
                else:
                    r = sie.importera_verifikationer(data, request.user.get_username())
                    messages.success(request, f"{r['nya']} verifikationer importerades som serie V. "
                                              f"{r['fanns']} fanns redan, {r['egna']} var skapade här från början "
                                              f"och hoppades över (av {r['totalt']} i filen).")
            except Exception as e:
                messages.error(request, f"Kunde inte läsa SIE-filen: {e}")
            return redirect(request.path)
        if request.method == "POST":
            messages.error(request, "Välj en SIE-fil.")
            return redirect(request.path)
        ctx.update(fran=idag.replace(month=1, day=1), till=idag)

    elif slug == "oss":
        fran = request.GET.get("fran") or str(idag.replace(month=((idag.month - 1) // 3) * 3 + 1, day=1))
        till = request.GET.get("till") or str(idag)
        rader = _oss(dt.date.fromisoformat(fran), dt.date.fromisoformat(till))
        if "exportera" in request.GET:
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
            w.writerow(["Land", "Momssats", "Beskattningsunderlag", "Moms"])
            for (land, sats), v in sorted(rader.items()):
                w.writerow([land, f"{sats:.2f}".replace(".", ","), f"{v['underlag']:.2f}".replace(".", ","),
                            f"{v['moms']:.2f}".replace(".", ",")])
            return _fil("﻿" + buf.getvalue(), f"oss_underlag_{fran}_{till}.csv")
        ctx.update(oss=sorted(rader.items()), fran=fran, till=till)

    return render(request, "bokforing/io_post.html", ctx)


def _oss(fran, till):
    """Försäljning till privatpersoner i andra EU-länder (underlag för OSS)."""
    from verksamhet.models import Faktura

    rader = defaultdict(lambda: {"underlag": Decimal(0), "moms": Decimal(0)})
    for f in Faktura.objects.filter(fakturadatum__range=(fran, till), status__in=["skickad", "betald"],
                                    kund__privatperson=True, kund__landkod__in=EU).select_related(
                                        "kund").prefetch_related("rader"):
        for sats, m in f.summering()["moms"].items():
            rader[(f.kund.landkod, Decimal(sats))]["underlag"] += m["underlag"]
            rader[(f.kund.landkod, Decimal(sats))]["moms"] += m["moms"]
    return rader


def _fakturaimport(request, slug, ctx):
    from . import fakturaimport as fi

    typ = "kund" if slug == "kundfakturor" else "lev"
    falt = fi.FALT[typ]
    if request.method == "POST" and typ == "kund" and request.FILES.getlist("pdfer"):
        from . import fakturapdf
        res = fakturapdf.koppla([(f.name, f.read()) for f in request.FILES.getlist("pdfer")])
        ok = [r for r in res if r["ok"]]
        messages.success(request, f"{len(ok)} av {len(res)} PDF:er kopplades till fakturor.")
        for r in res:
            if not r["ok"] or r["text"] not in ("kopplad", "redan kopplad"):
                (messages.warning if r["ok"] else messages.error)(
                    request, f"{r['fil']}: " + (f"faktura {r['faktura'].nummer} – " if r.get("faktura") else "") + r["text"])
        return redirect(request.path)
    if request.method == "POST" and "rensa" in request.POST:
        antal = fi.rensa_visma(typ)
        messages.success(request, f"{antal} tidigare importerade Visma-fakturor togs bort. Importera filen igen.")
        return redirect(request.path)
    if request.method == "POST" and request.FILES.get("fil"):
        try:
            fil = request.FILES["fil"]
            innehall = fil.read()
            if fi.ar_sie(innehall):
                if b"#VER" not in innehall:
                    raise fi.ImportFel(fi.TOM_SIE)
                nyckel = fi.spara_tillfalligt(typ, None, None, innehall)
            else:
                rubriker, rader = fi.las_fil(innehall, fil.name)
                nyckel = fi.spara_tillfalligt(typ, rubriker, rader)
            return redirect(f"{request.path}?import={nyckel}")
        except fi.ImportFel as e:
            messages.error(request, str(e))
            return redirect(request.path)
    if nyckel := request.GET.get("import") or request.POST.get("import"):
        try:
            data, fil = fi.hamta_tillfalligt(nyckel)
        except fi.ImportFel as e:
            messages.error(request, str(e))
            return redirect(request.path)
        if data.get("sie"):
            import base64
            poster, fel, antal_bet = fi.fran_sie(base64.b64decode(data["sie"]), typ)
            if request.method == "POST" and "importera" in request.POST:
                res = (fi.importera_kundfakturor if typ == "kund" else fi.importera_levfakturor)(poster)
                fil.unlink(missing_ok=True)
                text = f"{res['nya']} fakturor importerades från SIE-filen, {res['uppdaterade']} uppdaterades"
                if res["nya_parter"]:
                    text += f", {res['nya_parter']} nya {'kunder' if typ == 'kund' else 'leverantörer'} skapades"
                if typ == "kund":
                    text += f". Nästa fakturanummer här blir {res['nasta_nummer']}"
                messages.success(request, text + ".")
                if fel:
                    messages.warning(request, f"{len(fel)} verifikationer kunde inte tolkas: " + "; ".join(fel[:5]))
                return redirect("faktura_lista" if typ == "kund" else "levfakturor")
            ctx.update(steg="mappning", sie=True, nyckel=nyckel, saknas=[], fel=fel, antal=len(poster),
                       antal_bet=antal_bet, forhand=poster[:25], antal_betalda=sum(1 for p in poster if p["betald"]),
                       summa_obetalt=sum((p["belopp"] for p in poster if not p["betald"]), Decimal(0)), typ=typ)
            return render(request, "bokforing/io_post.html", ctx)
        rubriker, rader = data["rubriker"], data["rader"]
        if request.method == "POST":
            mform = mappningsform(falt, rubriker)(request.POST)
            if not mform.is_valid():
                messages.error(request, "Ogiltig kolumnmappning.")
                return redirect(f"{request.path}?import={nyckel}")
            mappning = {k: mform.cleaned_data[f"m_{k}"] for k, *_ in falt if mform.cleaned_data[f"m_{k}"] is not None}
        else:
            mappning = fi.gissa_mappning(typ, rubriker)
        saknas = [namn for k, namn, krav, _ in falt if krav and k not in mappning]
        poster, fel = fi.tolka(typ, rader, mappning) if not saknas else ([], [])
        if request.method == "POST" and "importera" in request.POST and not saknas:
            res = (fi.importera_kundfakturor if typ == "kund" else fi.importera_levfakturor)(poster)
            fil.unlink(missing_ok=True)
            text = f"{res['nya']} fakturor importerades, {res['uppdaterade']} uppdaterades"
            if res["nya_parter"]:
                text += f", {res['nya_parter']} nya {'kunder' if typ == 'kund' else 'leverantörer'} skapades"
            if typ == "kund":
                text += f". Nästa fakturanummer här blir {res['nasta_nummer']}"
            messages.success(request, text + ".")
            if fel:
                messages.warning(request, f"{len(fel)} rader hoppades över: " + "; ".join(fel[:5]))
            return redirect("faktura_lista" if typ == "kund" else "levfakturor")
        ctx.update(steg="mappning", rubriker=list(enumerate(rubriker)), falt=falt, mappning=mappning,
                   nyckel=nyckel, saknas=saknas, fel=fel, antal=len(poster),
                   forhand=poster[:8], antal_betalda=sum(1 for p in poster if p["betald"]),
                   summa_obetalt=sum((p["belopp"] for p in poster if not p["betald"] and not p["makulerad"]),
                                     Decimal(0)), typ=typ)
    else:
        from verksamhet.models import Faktura

        from .models import Leverantorsfaktura
        importerade = (Faktura if typ == "kund" else Leverantorsfaktura).objects.filter(fran_visma=True).count()
        ctx.update(steg="ladda", typ=typ, falt=falt, importerade=importerade)
    return render(request, "bokforing/io_post.html", ctx)


def _betalningsimport(request, ctx):
    import base64

    from . import fakturaimport as fi

    if request.method == "POST" and request.FILES.get("fil"):
        innehall = request.FILES["fil"].read()
        if not fi.ar_sie(innehall):
            messages.error(request, "Filen ser inte ut som en SIE-fil.")
            return redirect(request.path)
        if b"#VER" not in innehall:
            messages.error(request, fi.TOM_SIE)
            return redirect(request.path)
        return redirect(f"{request.path}?import={fi.spara_tillfalligt('betalning', None, None, innehall)}")
    if nyckel := request.GET.get("import") or request.POST.get("import"):
        try:
            data, fil = fi.hamta_tillfalligt(nyckel)
        except fi.ImportFel as e:
            messages.error(request, str(e))
            return redirect(request.path)
        res = fi.betalningar_fran_sie(base64.b64decode(data["sie"]))
        if request.method == "POST" and "importera" in request.POST:
            kund, lev = fi.registrera_betalningar(res)
            fil.unlink(missing_ok=True)
            messages.success(request, f"{kund} kundfakturor och {lev} leverantörsfakturor markerades som betalda.")
            return redirect("oversikt")
        ctx.update(steg="betalningar", nyckel=nyckel, res=res,
                   matchade=sum(1 for r in res if r["faktura"]), omatchade=sum(1 for r in res if not r["faktura"]))
    else:
        ctx["steg"] = "betalningar_ladda"
    return render(request, "bokforing/io_post.html", ctx)
