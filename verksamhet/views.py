import csv
import datetime as dt
from collections import OrderedDict, defaultdict
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from register.models import Foretag, Konto, Kund

from . import fakturadokument
from .forms import (
    BetaldForm,
    FakturaHuvudForm,
    FakturaradFormSet,
    FaktureraTidForm,
    TidcellForm,
    TidpostForm,
    UppdragForm,
)
from .models import Faktura, Fakturarad, Tidpost, Uppdrag, avrunda

ENHET_TEXT = {"HUR": "tim", "PCE": "st", "DAY": "dag", "MON": "mån"}


# ---------------------------------------------------------------- översikt

from .oversikt import oversikt  # noqa: E402,F401

# ---------------------------------------------------------------- tidrapport

def _vecka_start(request):
    try:
        d = dt.date.fromisoformat(request.GET.get("vecka", ""))
    except ValueError:
        d = timezone.localdate()
    return d - dt.timedelta(days=d.weekday())


def tidrapport(request):
    start = _vecka_start(request)
    dagar = [start + dt.timedelta(days=i) for i in range(7)]
    poster = Tidpost.objects.filter(datum__range=(dagar[0], dagar[-1])).select_related("uppdrag__kund")

    if request.method == "POST":
        form = TidpostForm(request.POST)
        if form.is_valid():
            tp = form.save()
            messages.success(request, f"Sparade {tp.timmar} h på {tp.uppdrag}.")
            return redirect(f"{request.path}?vecka={tp.datum.isoformat()}")
    else:
        senaste = Tidpost.objects.filter(uppdrag__aktiv=True).order_by("-skapad").first()
        aktiva = Uppdrag.objects.filter(aktiv=True)
        forval = senaste.uppdrag_id if senaste else (aktiva.first().pk if aktiva.count() == 1 else None)
        form = TidpostForm(initial={"uppdrag": forval,
                                    "datum": timezone.localdate() if timezone.localdate() in dagar else dagar[0]})

    # Rutnät: aktiva uppdrag + uppdrag med tid den här veckan
    uppdrag_ids = set(Uppdrag.objects.filter(aktiv=True).values_list("id", flat=True))
    uppdrag_ids |= {p.uppdrag_id for p in poster}
    rader = []
    for u in Uppdrag.objects.filter(id__in=uppdrag_ids).select_related("kund"):
        celler = []
        for d in dagar:
            pd = [p for p in poster if p.uppdrag_id == u.id and p.datum == d]
            celler.append({
                "datum": d,
                "timmar": sum((p.timmar for p in pd), Decimal(0)),
                "last": any(p.fakturerad for p in pd) or len(pd) > 1,
            })
        rader.append({"uppdrag": u, "celler": celler, "summa": sum(c["timmar"] for c in celler)})
    dagsummor = [sum((p.timmar for p in poster if p.datum == d), Decimal(0)) for d in dagar]

    return render(request, "verksamhet/tidrapport.html", {
        "form": form, "dagar": dagar, "rader": rader, "dagsummor": dagsummor,
        "veckosumma": sum(dagsummor), "poster": poster,
        "veckonummer": start.isocalendar()[1],
        "foregaende": start - dt.timedelta(days=7), "nasta": start + dt.timedelta(days=7),
        "idag": timezone.localdate(),
    })


@require_POST
def tid_cell(request):
    """HTMX: sätter antal timmar för ett uppdrag en viss dag (från veckorutnätet)."""
    form = TidcellForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(" ".join(e for fel in form.errors.values() for e in fel))
    uppdrag, datum, timmar = (form.cleaned_data[k] for k in ("uppdrag", "datum", "timmar"))

    befintliga = Tidpost.objects.filter(uppdrag=uppdrag, datum=datum)
    if befintliga.filter(fakturarad__isnull=False).exists() or befintliga.count() > 1:
        return HttpResponseBadRequest("Dagen har fakturerad tid eller flera poster – ändra i listan")
    post = befintliga.first()
    if timmar == 0:
        if post:
            post.delete()
    elif post:
        post.timmar = timmar
        post.save(update_fields=["timmar"])
    else:
        Tidpost.objects.create(uppdrag=uppdrag, datum=datum, timmar=timmar)
    return HttpResponse(status=204)


def tidpost_andra(request, pk):
    tp = get_object_or_404(Tidpost, pk=pk)
    if tp.fakturerad:
        messages.error(request, "Tidposten är fakturerad och kan inte ändras.")
        return redirect(f"/tid/?vecka={tp.datum.isoformat()}")
    form = TidpostForm(request.POST or None, instance=tp)
    if request.method == "POST":
        if "radera" in request.POST:
            tp.delete()
            messages.success(request, "Tidposten raderades.")
            return redirect(f"/tid/?vecka={tp.datum.isoformat()}")
        if form.is_valid():
            form.save()
            return redirect(f"/tid/?vecka={tp.datum.isoformat()}")
    return render(request, "verksamhet/form.html", {"form": form, "rubrik": "Ändra tidpost", "radera": True})


def tid_export_csv(request):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="tidrapport.csv"'
    resp.write("﻿")
    w = csv.writer(resp, delimiter=";")
    w.writerow(["Datum", "Kund", "Uppdrag", "Timmar", "Beskrivning", "Fakturerbar", "Fakturanummer"])
    for t in Tidpost.objects.select_related("uppdrag__kund", "fakturarad__faktura").order_by("datum"):
        w.writerow([t.datum, t.uppdrag.kund.namn, t.uppdrag.namn, str(t.timmar).replace(".", ","),
                    t.beskrivning, "Ja" if t.fakturerbar else "Nej",
                    t.fakturarad.faktura.nummer if t.fakturarad else ""])
    return resp


# ---------------------------------------------------------------- uppdrag

def uppdrag_lista(request):
    visa_alla = request.GET.get("alla") == "1"
    qs = Uppdrag.objects.select_related("kund", "artikel")
    if not visa_alla:
        qs = qs.filter(aktiv=True)
    lista = list(qs)
    for u in lista:
        u.ofakt = u.timmar_ofakturerade()
        u.totalt = u.timmar_totalt()
    return render(request, "verksamhet/uppdrag_lista.html", {"uppdrag": lista, "visa_alla": visa_alla})


def uppdrag_form(request, pk=None):
    obj = get_object_or_404(Uppdrag, pk=pk) if pk else None
    initial = {}
    if not obj:
        f = Foretag.hamta()
        initial = {"artikel": f.standardartikel_id, "kund": request.GET.get("kund")}
    form = UppdragForm(request.POST or None, instance=obj, initial=initial)
    if request.method == "POST" and form.is_valid():
        u = form.save()
        messages.success(request, f"Uppdraget {u.namn} sparades.")
        return redirect(u)
    return render(request, "verksamhet/form.html", {
        "form": form, "rubrik": f"Ändra uppdrag: {obj.namn}" if obj else "Nytt uppdrag"})


def uppdrag_detalj(request, pk):
    u = get_object_or_404(Uppdrag.objects.select_related("kund", "artikel"), pk=pk)
    poster = u.tidposter.select_related("fakturarad__faktura")
    return render(request, "verksamhet/uppdrag_detalj.html", {
        "u": u, "poster": poster, "totalt": u.timmar_totalt(), "ofakt": u.timmar_ofakturerade()})


# ---------------------------------------------------------------- kunder

def kund_detalj(request, pk):
    kund = get_object_or_404(Kund, pk=pk)
    fakturor = kund.fakturor.prefetch_related("rader")
    return render(request, "verksamhet/kund_detalj.html", {
        "kund": kund, "uppdrag": kund.uppdrag.all(), "fakturor": fakturor})


# ---------------------------------------------------------------- fakturor

def faktura_lista(request):
    status = request.GET.get("status", "")
    qs = Faktura.objects.select_related("kund").prefetch_related("rader", "bilagor")
    if status:
        qs = qs.filter(status=status)
    return render(request, "verksamhet/faktura_lista.html", {
        "fakturor": qs, "status": status, "statusval": Faktura.STATUS})


def _rad_fran_uppdrag(faktura, uppdrag, timmar, beskrivning, ordning):
    a = uppdrag.artikel
    konto = a.intaktskonto if a else Konto.objects.filter(pk=3041).first()
    return Fakturarad.objects.create(
        faktura=faktura, artikel=a, beskrivning=beskrivning, antal=timmar,
        enhet=ENHET_TEXT.get(a.enhet, "tim") if a else "tim", a_pris=uppdrag.pris,
        momssats=a.momssats if a else Decimal(25), konto=konto,
        kostnadsstalle=uppdrag.kostnadsstalle, ordning=ordning,
    )


def fakturera_tid(request):
    idag = timezone.localdate()
    forra_manad_slut = idag.replace(day=1) - dt.timedelta(days=1)
    form = FaktureraTidForm(request.POST or None, initial={
        "kund": request.GET.get("kund"), "till_och_med": forra_manad_slut, "fakturadatum": idag})
    forhandsvisning = None

    if form.is_valid():
        kund = form.cleaned_data["kund"]
        poster = list(Tidpost.objects.filter(
            uppdrag__kund=kund, fakturerbar=True, fakturarad__isnull=True,
            datum__lte=form.cleaned_data["till_och_med"]).select_related("uppdrag__artikel").order_by("datum"))
        if "skapa" in request.POST and poster:
            with transaction.atomic():
                forsta_uppdrag = poster[0].uppdrag
                f = Faktura.objects.create(
                    kund=kund, fakturadatum=form.cleaned_data["fakturadatum"],
                    er_referens=forsta_uppdrag.kundreferens or kund.kontaktperson,
                    var_referens=Foretag.hamta().var_referens, ordernummer=forsta_uppdrag.ordernummer,
                    period=_periodtext(poster[0].datum, poster[-1].datum),
                )
                grupper = OrderedDict()
                for p in poster:
                    grupper.setdefault(p.uppdrag, []).append(p)
                ordning = 0
                for uppdrag, pp in grupper.items():
                    if form.cleaned_data["en_rad_per_post"]:
                        for p in pp:
                            ordning += 1
                            text = f"{p.datum:%Y-%m-%d} {uppdrag.namn}" + (f": {p.beskrivning}" if p.beskrivning else "")
                            rad = _rad_fran_uppdrag(f, uppdrag, p.timmar, text, ordning)
                            p.fakturarad = rad
                            p.save(update_fields=["fakturarad"])
                    else:
                        ordning += 1
                        text = f"{uppdrag.artikel.namn if uppdrag.artikel else uppdrag.namn}, " \
                               f"{_periodtext(pp[0].datum, pp[-1].datum)}"
                        if uppdrag.artikel and uppdrag.artikel.namn != uppdrag.namn:
                            text = f"{uppdrag.namn} – {text}"
                        rad = _rad_fran_uppdrag(f, uppdrag, sum(p.timmar for p in pp), text, ordning)
                        Tidpost.objects.filter(id__in=[p.id for p in pp]).update(fakturarad=rad)
            messages.success(request, "Fakturautkast skapat. Kontrollera och slutför.")
            return redirect(f)
        sammanstallning = defaultdict(lambda: {"timmar": Decimal(0), "belopp": Decimal(0)})
        for p in poster:
            s = sammanstallning[p.uppdrag]
            s["timmar"] += p.timmar
            s["belopp"] += avrunda(p.timmar * p.uppdrag.pris)
        forhandsvisning = {"poster": poster, "per_uppdrag": dict(sammanstallning),
                           "netto": sum((s["belopp"] for s in sammanstallning.values()), Decimal(0))}
    return render(request, "verksamhet/fakturera_tid.html", {"form": form, "f": forhandsvisning})


MANADER = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september",
           "oktober", "november", "december"]


def _periodtext(fran, till):
    if (fran.year, fran.month) == (till.year, till.month):
        return f"{MANADER[fran.month - 1]} {fran.year}".capitalize()
    return f"{fran:%Y-%m-%d} – {till:%Y-%m-%d}"


def faktura_ny(request):
    f = Foretag.hamta()
    form = FakturaHuvudForm(request.POST or None, initial={
        "kund": request.GET.get("kund"), "var_referens": f.var_referens})
    if request.method == "POST" and form.is_valid():
        faktura = form.save()
        return redirect(faktura)
    return render(request, "verksamhet/form.html", {"form": form, "rubrik": "Ny faktura (manuell)"})


def faktura_detalj(request, pk):
    faktura = get_object_or_404(Faktura.objects.select_related("kund"), pk=pk)
    if request.method == "POST" and request.FILES.get("original"):
        from bokforing import fakturapdf
        fil = request.FILES["original"]
        res = fakturapdf.koppla([(fil.name, fil.read())])[0]
        if res.get("faktura") and res["faktura"].pk != faktura.pk:
            messages.error(request, f"PDF:en gäller faktura {res['faktura'].nummer} och kopplades dit.")
        else:
            (messages.success if res["ok"] else messages.error)(request, f"Original: {res['text']}")
        return redirect(faktura)
    huvud = FakturaHuvudForm(request.POST or None, instance=faktura, prefix="h")
    rader = FakturaradFormSet(request.POST or None, instance=faktura, prefix="r")
    for rf in rader.forms:
        rf.fields["konto"].queryset = Konto.objects.filter(aktiv=True, nummer__gte=3000, nummer__lt=4000)
        rf.fields["artikel"].widget.attrs["class"] = "artikelval"

    if request.method == "POST":
        if faktura.last:
            messages.error(request, "Fakturan är låst.")
            return redirect(faktura)
        if huvud.is_valid() and rader.is_valid():
            with transaction.atomic():
                huvud.save()
                rader.save()
            messages.success(request, "Fakturan sparades.")
            return redirect(faktura)
    return render(request, "verksamhet/faktura_detalj.html", {
        "faktura": faktura, "huvud": huvud, "rader": rader, "sum": faktura.summering(),
        "betald_form": BetaldForm(initial={"betaldatum": timezone.localdate()}),
        "artiklar_json": _artiklar_json(),
        "drojsmal": fakturadokument.drojsmalsranta(faktura.summering()["att_betala"], faktura.forfallodatum,
                                                   timezone.localdate()) if faktura.forfallen else None,
        "mottagare": fakturadokument.mottagare(faktura.kund)[0],
    })


def _artiklar_json():
    from register.models import Artikel
    return {str(a.pk): {"namn": a.namn, "pris": str(a.pris), "enhet": ENHET_TEXT.get(a.enhet, a.enhet),
                        "moms": str(a.momssats), "konto": a.intaktskonto_id}
            for a in Artikel.objects.filter(aktiv=True)}


@require_POST
def faktura_atgard(request, pk, atgard):
    from bokforing import tjanster as bok

    f = get_object_or_404(Faktura, pk=pk)
    anv = request.user.get_username()
    try:
        with transaction.atomic():
            if atgard == "slutfor" and f.status == Faktura.UTKAST:
                if not f.rader.exists():
                    messages.error(request, "Fakturan har inga rader.")
                    return redirect(f)
                f.slutfor()
                ver = bok.bokfor_kundfaktura(f, anv)
                messages.success(request, f"Faktura {f.nummer} är klar och bokförd som {ver.vernr}. "
                                          f"Förfaller {f.forfallodatum}.")
            elif atgard == "bokfor" and f.status in (Faktura.SKICKAD, Faktura.BETALD) and not f.fran_visma:
                ver = bok.bokfor_kundfaktura(f, anv)
                if ver:
                    messages.success(request, f"Fakturan bokfördes som {ver.vernr}.")
                if f.status == Faktura.BETALD and f.betaldatum and not f.verifikationer.filter(serie="I").exists():
                    ver = bok.bokfor_inbetalning(f, f.betaldatum, anvandare=anv)
                    messages.success(request, f"Inbetalningen bokfördes som {ver.vernr}.")
            elif atgard == "betald" and f.status == Faktura.SKICKAD:
                form = BetaldForm(request.POST)
                if form.is_valid():
                    f.betaldatum = form.cleaned_data["betaldatum"]
                    f.status = Faktura.BETALD
                    f.save()
                    ver = bok.bokfor_inbetalning(f, f.betaldatum, anvandare=anv)
                    messages.success(request, f"Faktura {f.nummer} markerad som betald ({ver.vernr}).")
            elif atgard == "obetald" and f.status == Faktura.BETALD:
                for ver in bok.aktiva_ver(f.verifikationer.filter(serie="I")):
                    bok.rattelse(ver, timezone.localdate(), anv)
                from bokforing.models import Bankpost
                Bankpost.objects.filter(verifikation__kundfaktura=f, verifikation__serie="I").update(verifikation=None)
                f.status, f.betaldatum = Faktura.SKICKAD, None
                f.save()
                messages.info(request, "Betalningen är återställd och inbetalningen rättad.")
            elif atgard == "makulera" and f.status in (Faktura.SKICKAD,):
                for ver in bok.aktiva_ver(f.verifikationer.all()):
                    bok.rattelse(ver, timezone.localdate(), anv, text=f"Makulering av {ver.vernr}: {ver.text}")
                Tidpost.objects.filter(fakturarad__faktura=f).update(fakturarad=None)
                f.status = Faktura.MAKULERAD
                f.save()
                messages.warning(request, f"Faktura {f.nummer} makulerad och bokföringen rättad. "
                                          "Tiden är åter ofakturerad. Om kunden redan fått fakturan bör du "
                                          "skicka en kreditfaktura.")
            elif atgard == "radera" and f.status == Faktura.UTKAST:
                Tidpost.objects.filter(fakturarad__faktura=f).update(fakturarad=None)
                f.delete()
                messages.success(request, "Utkastet raderades och tiden är åter ofakturerad.")
                return redirect("faktura_lista")
    except bok.BokforingsFel as e:
        messages.error(request, f"Kunde inte bokföra: {e}")
    return redirect(f)


def faktura_utskrift(request, pk):
    """Fakturan som PDF: originalet om det finns (Visma), annars skapad här. ?html=1 ger utskriftssidan."""
    f = get_object_or_404(Faktura.objects.select_related("kund__betalningsvillkor"), pk=pk)
    if (original := f.bilagor.order_by("-uppladdad").first()) and "genererad" not in request.GET:
        return redirect("bilaga", pk=original.pk)
    if "html" in request.GET:
        return render(request, "verksamhet/faktura_utskrift.html", {
            "f": f, "sum": f.summering(), "rader": f.rader.all()})
    data = fakturadokument.faktura_pdf(f, Foretag.hamta())
    return _pdfsvar(data, f"Faktura_{f.nummer or 'utkast'}.pdf", "ladda" in request.GET)


def _pdfsvar(data, filnamn, som_bilaga=False):
    svar = HttpResponse(data, content_type="application/pdf")
    svar["Content-Disposition"] = f'{"attachment" if som_bilaga else "inline"}; filename="{filnamn}"'
    return svar


def _emlsvar(data, filnamn):
    svar = HttpResponse(data, content_type="message/rfc822")
    svar["Content-Disposition"] = f'attachment; filename="{filnamn}"'
    return svar


@require_POST
def faktura_epost(request, pk):
    """E-postutkast (.eml) med fakturan bifogad. Öppnas i Outlook som ett nytt mejl."""
    f = get_object_or_404(Faktura.objects.select_related("kund"), pk=pk)
    if not f.nummer:
        messages.error(request, "Slutför fakturan innan den skickas.")
        return redirect(f)
    foretag = Foretag.hamta()
    original = f.bilagor.order_by("-uppladdad").first()
    pdf = original.fil.read() if original else fakturadokument.faktura_pdf(f, foretag)
    data = fakturadokument.faktura_eml(f, foretag, pdf)
    Faktura.objects.filter(pk=f.pk).update(utskickad=timezone.now())
    return _emlsvar(data, f"Faktura_{f.nummer}.eml")


@require_POST
def faktura_paminnelse(request, pk):
    f = get_object_or_404(Faktura.objects.select_related("kund"), pk=pk, status=Faktura.SKICKAD)
    foretag = Foretag.hamta()
    idag = timezone.localdate()
    try:
        avgift = Decimal((request.POST.get("avgift") or "0").replace(",", "."))
    except Exception:
        avgift = Decimal(0)
    pdf, _, _ = fakturadokument.paminnelse_pdf(f, foretag, idag, max(avgift, Decimal(0)))
    Faktura.objects.filter(pk=f.pk).update(paminnelser=f.paminnelser + 1, senaste_paminnelse=idag)
    if request.POST.get("format") == "pdf":
        return _pdfsvar(pdf, f"Paminnelse_faktura_{f.nummer}.pdf", True)
    return _emlsvar(fakturadokument.paminnelse_eml(f, foretag, pdf), f"Paminnelse_faktura_{f.nummer}.eml")


# ---------------------------------------------------------------- export

def fakturor_csv(request):
    idag = timezone.localdate()
    try:
        fran = dt.date.fromisoformat(request.GET.get("fran", ""))
        till = dt.date.fromisoformat(request.GET.get("till", ""))
    except ValueError:
        fran, till = idag.replace(month=1, day=1), idag
    return _fakturor_csv(fran, till)


def _fakturor_csv(fran, till):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="fakturor.csv"'
    resp.write("﻿")
    w = csv.writer(resp, delimiter=";")
    w.writerow(["Fakturanummer", "Fakturadatum", "Förfallodatum", "Kundnummer", "Kund", "Status",
                "Netto", "Moms", "Öresavrundning", "Att betala", "Betaldatum"])
    for f in Faktura.objects.filter(fakturadatum__range=(fran, till)).exclude(status=Faktura.UTKAST) \
            .select_related("kund").prefetch_related("rader").order_by("nummer"):
        s = f.summering()
        w.writerow([f.nummer, f.fakturadatum, f.forfallodatum, f.kund.nummer, f.kund.namn, f.get_status_display(),
                    *[str(x).replace(".", ",") for x in (s["netto"], s["moms_total"], s["oresavrundning"],
                                                         s["att_betala"])],
                    f.betaldatum or ""])
    return resp


# ---------------------------------------------------------------- sök

def sok(request):
    from bokforing.models import Leverantorsfaktura, Verifikation
    from lon.models import Anstalld
    from register.models import Leverantor

    q = request.GET.get("q", "").strip()
    traffar = []
    if q:
        for k in Kund.objects.filter(Q(namn__icontains=q) | Q(orgnr__icontains=q))[:10]:
            traffar.append(("Kund", str(k), f"/kunder/{k.pk}/"))
        for l in Leverantor.objects.filter(Q(namn__icontains=q) | Q(orgnr__icontains=q))[:10]:
            traffar.append(("Leverantör", str(l), f"/register/leverantorer/{l.pk}/"))
        ff = Faktura.objects.select_related("kund").filter(Q(kund__namn__icontains=q) | Q(er_referens__icontains=q))
        if q.isdigit():
            ff = ff | Faktura.objects.filter(nummer=int(q))
        for f in ff.distinct()[:10]:
            traffar.append(("Kundfaktura", str(f), f.get_absolute_url()))
        for u in Uppdrag.objects.filter(namn__icontains=q).select_related("kund")[:10]:
            traffar.append(("Uppdrag", str(u), u.get_absolute_url()))
        for lf in Leverantorsfaktura.objects.filter(Q(motpart__icontains=q) | Q(leverantor__namn__icontains=q) |
                                                    Q(beskrivning__icontains=q) | Q(fakturanummer=q))[:10]:
            traffar.append(("Inköp", f"{lf.fakturadatum} {lf}", lf.get_absolute_url()))
        vq = Q(text__icontains=q)
        if q[:1].isalpha() and q[1:].isdigit():
            vq |= Q(serie=q[0].upper(), nummer=int(q[1:]))
        for v in Verifikation.objects.filter(vq)[:15]:
            traffar.append(("Verifikation", f"{v.vernr} {v.datum} {v.text}", v.get_absolute_url()))
        for a in Anstalld.objects.filter(Q(fornamn__icontains=q) | Q(efternamn__icontains=q))[:10]:
            traffar.append(("Anställd", a.namn, a.get_absolute_url()))
    return render(request, "verksamhet/sok.html", {"q": q, "traffar": traffar})


def uppdragsuppfoljning(request):
    from . import uppfoljning
    idag = timezone.localdate()
    try:
        ar = int(request.GET.get("ar", idag.year))
    except ValueError:
        ar = idag.year
    bel = uppfoljning.belaggning(ar, idag)
    kunder, total = uppfoljning.per_kund(ar)
    MAN = ["Jan", "Feb", "Mar", "Apr", "Maj", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]
    for r in bel["manader"]:
        r["namn"] = MAN[r["manad"] - 1]
    diagram = {"kategorier": [MAN[r["manad"] - 1] for r in bel["manader"]],
               "timmar": [float(r["timmar"]) for r in bel["manader"]],
               "mojliga": [float(r["mojliga"]) for r in bel["manader"]]}
    return render(request, "verksamhet/uppdragsuppfoljning.html", {
        "ar": ar, "ar_lista": [idag.year, idag.year - 1, idag.year - 2], "bel": bel, "kunder": kunder,
        "total": total, "status": uppfoljning.uppdrag_status(idag), "diagram": diagram})
