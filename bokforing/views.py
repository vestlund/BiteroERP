import datetime as dt
from collections import OrderedDict, defaultdict
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from config.formular import nasta_eller
from register.models import Foretag, Konto, Leverantor
from verksamhet.models import Faktura

from . import sie
from . import tjanster as bok
from .bankimport import BankimportFel, las_kontoutdrag
from .forms import (
    BankimportForm,
    BankpostAtgardForm,
    BetalaForm,
    LevfakturaForm,
    MomsperiodForm,
    PeriodForm,
    SieExportForm,
    SieImportForm,
    VerifikationForm,
    VerRadFormSet,
)
from .models import Bankpost, Bilaga, IngaendeBalans, Leverantorsfaktura, Transaktion, Verifikation

ANV = lambda request: request.user.get_username()  # noqa: E731


def _spara_bilaga(fil, **koppling):
    if fil:
        Bilaga.objects.create(fil=fil, namn=fil.name, **koppling)


def _konton_json():
    return list(Konto.objects.filter(aktiv=True).values_list("nummer", "namn"))


# ------------------------------------------------------------ verifikationer

def verifikationer(request):
    qs = Verifikation.objects.prefetch_related("transaktioner", "bilagor", "rattelser")
    ar = request.GET.get("ar") or str(timezone.localdate().year)
    if ar != "alla":
        qs = qs.filter(ar=int(ar))
    if s := request.GET.get("serie"):
        qs = qs.filter(serie=s)
    if q := request.GET.get("q", "").strip():
        filt = Q(text__icontains=q) | Q(transaktioner__text__icontains=q)
        if q.isdigit():
            filt |= Q(nummer=int(q)) | Q(transaktioner__konto_id=int(q))
        try:
            filt |= Q(transaktioner__belopp=Decimal(q.replace(",", "."))) | \
                Q(transaktioner__belopp=-Decimal(q.replace(",", ".")))
        except Exception:
            pass
        qs = qs.filter(filt).distinct()
    sida = Paginator(qs, 50).get_page(request.GET.get("sida"))
    ar_lista = sorted(set(Verifikation.objects.values_list("ar", flat=True)) | {timezone.localdate().year},
                      reverse=True)
    return render(request, "bokforing/verifikationer.html", {
        "sida": sida, "serier": Verifikation.SERIER, "ar_lista": ar_lista, "ar": ar,
        "serie": request.GET.get("serie", ""), "q": q})


def verifikation(request, pk):
    v = get_object_or_404(Verifikation.objects.prefetch_related("transaktioner__konto", "bilagor"), pk=pk)
    if request.method == "POST" and request.FILES.get("bilaga"):
        _spara_bilaga(request.FILES["bilaga"], verifikation=v)
        messages.success(request, "Bilagan lades till.")
        return redirect(v)
    t = list(v.transaktioner.all())
    return render(request, "bokforing/verifikation.html", {
        "v": v, "transaktioner": t, "debet": sum((x.debet or 0 for x in t), Decimal(0)),
        "kredit": sum((x.kredit or 0 for x in t), Decimal(0)), "rattelser": v.rattelser.all(),
        "bankposter": v.bankposter.all()})


@require_POST
def verifikation_ratta(request, pk):
    v = get_object_or_404(Verifikation, pk=pk)
    try:
        r = bok.rattelse(v, timezone.localdate(), ANV(request))
        Bankpost.objects.filter(verifikation=v).update(verifikation=None)
        if v.levfaktura_id and v.serie == "U":
            Leverantorsfaktura.objects.filter(pk=v.levfaktura_id).update(betaldatum=None)
        messages.success(request, f"{v.vernr} är rättad med {r.vernr}. Bokför nu den korrekta versionen vid behov.")
        return redirect(r)
    except bok.BokforingsFel as e:
        messages.error(request, str(e))
        return redirect(v)


def verifikation_ny(request):
    kopia = None
    if kopiera := request.GET.get("kopiera"):
        kopia = Verifikation.objects.filter(pk=kopiera).first()
    if request.method == "POST":
        form = VerifikationForm(request.POST, request.FILES)
        rader = VerRadFormSet(request.POST, prefix="r")
        if form.is_valid() and rader.is_valid():
            r = [{"konto": f.cleaned_data["konto"], "belopp": f.belopp, "text": f.cleaned_data.get("text", "")}
                 for f in rader.forms if f.cleaned_data.get("konto") and f.belopp]
            try:
                with transaction.atomic():
                    v = bok.bokfor(form.cleaned_data["datum"], form.cleaned_data["text"], r, "A", ANV(request))
                    _spara_bilaga(form.cleaned_data.get("bilaga"), verifikation=v)
                messages.success(request, f"Verifikation {v.vernr} bokförd.")
                return redirect("verifikation_ny" if "fler" in request.POST else v.get_absolute_url())
            except bok.BokforingsFel as e:
                form.add_error(None, str(e))
    else:
        initial = []
        if kopia:
            initial = [{"konto": t.konto_id, "debet": t.debet, "kredit": t.kredit, "text": t.text}
                       for t in kopia.transaktioner.all()]
        form = VerifikationForm(initial={"text": kopia.text if kopia else ""})
        rader = VerRadFormSet(prefix="r", initial=initial)
    return render(request, "bokforing/verifikation_ny.html", {
        "form": form, "rader": rader, "konton": _konton_json(), "mallar": bok.MALLAR})


def bilaga(request, pk):
    b = get_object_or_404(Bilaga, pk=pk)
    try:
        return FileResponse(b.fil.open("rb"), filename=b.namn, as_attachment="ladda" in request.GET)
    except FileNotFoundError:
        raise Http404("Filen saknas")


# ------------------------------------------------------- leverantörsfakturor

def levfakturor(request):
    qs = Leverantorsfaktura.objects.select_related("leverantor", "konto").prefetch_related("bilagor")
    visa = request.GET.get("visa", "obetalda")
    if visa == "obetalda":
        qs = qs.filter(betaldatum__isnull=True, typ=Leverantorsfaktura.FAKTURA)
    elif visa == "kvitton":
        qs = qs.filter(typ=Leverantorsfaktura.KVITTO)
    obetalt = Leverantorsfaktura.objects.filter(betaldatum__isnull=True, typ="faktura").aggregate(
        s=Sum("belopp"))["s"] or 0
    obetalt = bok._d(obetalt)
    return render(request, "bokforing/levfakturor.html", {
        "sida": Paginator(qs, 50).get_page(request.GET.get("sida")), "visa": visa, "obetalt": obetalt})


def levfaktura_ny(request):
    typ = request.GET.get("typ", Leverantorsfaktura.FAKTURA)
    if request.method == "POST":
        form = LevfakturaForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                with transaction.atomic():
                    lf = form.save()
                    _spara_bilaga(form.cleaned_data.get("bilaga"), levfaktura=lf)
                    ver = bok.bokfor_levfaktura(lf, ANV(request))
                    if lf.leverantor and not lf.leverantor.standardkonto_id:
                        Leverantor.objects.filter(pk=lf.leverantor_id).update(standardkonto=lf.konto)
                    # Bilagan hör till verifikationen också
                    Bilaga.objects.filter(levfaktura=lf).update(verifikation=ver)
                messages.success(request, f"{lf.get_typ_display()} bokförd som {ver.vernr}.")
                if "fler" in request.POST:
                    return redirect(f"{request.path}?typ={lf.typ}")
                return redirect(lf)
            except bok.BokforingsFel as e:
                form.add_error(None, str(e))
    else:
        form = LevfakturaForm(initial={"typ": typ, "fakturadatum": timezone.localdate()})
    lev = {l.pk: {"konto": l.standardkonto_id or _senaste_konto(l), "moms": str(l.standard_momssats),
                  "dagar": l.betalningsvillkor.dagar if l.betalningsvillkor else 30}
           for l in Leverantor.objects.filter(aktiv=True).select_related("betalningsvillkor")}
    return render(request, "bokforing/levfaktura_ny.html", {"form": form, "lev": lev, "typ": typ})


def _senaste_konto(lev):
    s = Leverantorsfaktura.objects.filter(leverantor=lev).order_by("-skapad").first()
    return s.konto_id if s else None


def levfaktura(request, pk):
    lf = get_object_or_404(Leverantorsfaktura.objects.select_related("leverantor", "konto"), pk=pk)
    if request.method == "POST" and request.FILES.get("bilaga"):
        ver = lf.verifikationer.filter(serie="L").first()
        _spara_bilaga(request.FILES["bilaga"], levfaktura=lf, verifikation=ver)
        return redirect(lf)
    return render(request, "bokforing/levfaktura.html", {
        "lf": lf, "vers": lf.verifikationer.prefetch_related("transaktioner__konto"),
        "bilagor": lf.bilagor.all(), "betala": BetalaForm()})


@require_POST
def levfaktura_betala(request, pk):
    lf = get_object_or_404(Leverantorsfaktura, pk=pk)
    form = BetalaForm(request.POST)
    if form.is_valid() and not lf.betald:
        try:
            ver = bok.bokfor_utbetalning(lf, form.cleaned_data["datum"], anvandare=ANV(request))
            messages.success(request, f"Betalningen bokförd som {ver.vernr}.")
        except bok.BokforingsFel as e:
            messages.error(request, str(e))
    return redirect(lf)


@require_POST
def levfaktura_makulera(request, pk):
    lf = get_object_or_404(Leverantorsfaktura, pk=pk)
    try:
        with transaction.atomic():
            for v in bok.aktiva_ver(lf.verifikationer.all()):
                bok.rattelse(v, timezone.localdate(), ANV(request), text=f"Makulering av {v.vernr}: {v.text}")
            Bankpost.objects.filter(verifikation__levfaktura=lf).update(verifikation=None)
            Leverantorsfaktura.objects.filter(pk=lf.pk).update(betaldatum=None)
        messages.warning(request, "Bokföringen är rättad. Registrera fakturan på nytt om något var fel.")
    except bok.BokforingsFel as e:
        messages.error(request, str(e))
    return redirect(lf)


# ---------------------------------------------------------------------- bank

def bank(request):
    from . import avstamning
    from .bankimport import ar_camt, las_camt

    form = BankimportForm()
    if request.method == "POST" and "matcha" in request.POST:
        res, fel = avstamning.matcha_alla(ANV(request))
        _visa_matchning(request, res, fel)
        return redirect("bank")
    if request.method == "POST":
        form = BankimportForm(request.POST, request.FILES)
        if form.is_valid():
            konto = form.cleaned_data["konto"]
            data = form.cleaned_data["fil"].read()
            try:
                if ar_camt(data):
                    poster, iban = las_camt(data, konto.nummer)
                else:
                    poster = las_kontoutdrag(data, konto.nummer)
                nya_id = []
                for p in poster:
                    obj, skapad = Bankpost.objects.get_or_create(nyckel=p["nyckel"], defaults={
                        "datum": p["datum"], "text": p["text"], "belopp": p["belopp"], "saldo": p["saldo"],
                        "konto": konto, "importfil": form.cleaned_data["fil"].name,
                        "referens": p.get("referens", ""), "end_to_end": p.get("end_to_end", ""),
                        "motpart": p.get("motpart", "")})
                    if skapad:
                        nya_id.append(obj.pk)
                messages.success(request, f"{len(nya_id)} nya bankposter importerades "
                                          f"({len(poster) - len(nya_id)} fanns redan).")
                res, fel = avstamning.matcha_alla(ANV(request), Bankpost.objects.filter(pk__in=nya_id))
                _visa_matchning(request, res, fel)
                return redirect("bank")
            except BankimportFel as e:
                form.add_error("fil", str(e))
    visa = request.GET.get("visa", "att_hantera")
    qs = Bankpost.objects.select_related("verifikation")
    if visa == "att_hantera":
        qs = qs.filter(verifikation__isnull=True, ignorerad=False).order_by("datum", "id")
    elif visa == "auto":
        qs = qs.filter(automatisk=True)
    sida = Paginator(qs, 40).get_page(request.GET.get("sida"))
    if visa == "att_hantera":
        for p in sida:
            p.forslag = bok.bankpost_forslag(p)
            p.regelforslag = avstamning.regler_for(p, bara_auto=False)
    antal = bok.ofakturerade_bankposter().count()
    return render(request, "bokforing/bank.html", {
        "form": form, "sida": sida, "visa": visa, "antal": antal, "konton": _konton_json(),
        "antal_auto": Bankpost.objects.filter(automatisk=True).count()})


def _visa_matchning(request, res, fel):
    if res:
        messages.success(request, f"{len(res)} poster stämdes av automatiskt: " +
                         "; ".join(f"{p.datum} {p.belopp:.2f} kr – {b}" for p, b in res[:8]) +
                         (" …" if len(res) > 8 else ""))
    for p, e in fel[:5]:
        messages.warning(request, f"{p.datum} {p.text[:40]}: {e}")


@require_POST
def bankpost_hantera(request, pk):
    from . import avstamning
    from .models import Matchningsregel

    p = get_object_or_404(Bankpost, pk=pk)
    form = BankpostAtgardForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Kunde inte hantera bankposten: " + _felen(form))
        return nasta_eller(request, "bank")
    d, anv = form.cleaned_data, ANV(request)
    atgard = d["atgard"]
    try:
        with transaction.atomic():
            if atgard == "kundfaktura":
                f = get_object_or_404(Faktura, pk=d["id"], status=Faktura.SKICKAD)
                f.status, f.betaldatum = Faktura.BETALD, p.datum
                f.save()
                if not f.fran_visma and not f.verifikationer.filter(serie="F").exists():
                    bok.bokfor_kundfaktura(f, anv)
                p.verifikation = bok.bokfor_inbetalning(f, p.datum, p.konto_id, anv)
            elif atgard == "levfaktura":
                lf = get_object_or_404(Leverantorsfaktura, pk=d["id"], betaldatum__isnull=True)
                p.verifikation = bok.bokfor_utbetalning(lf, p.datum, p.konto_id, anv)
            elif atgard == "ver":
                p.verifikation = get_object_or_404(Verifikation, pk=d["id"])
            elif atgard == "konto":
                motkonto = d["motkonto"].pk
                p.verifikation = bok.bokfor(p.datum, d["text"] or p.text,
                                            [(p.konto_id, p.belopp), (motkonto, -p.belopp)], "B", anv)
                if d["spara_regel"]:
                    regeltext = d["regeltext"].strip()
                    Matchningsregel.objects.create(
                        namn=regeltext, text_innehaller=regeltext, riktning="in" if p.belopp > 0 else "ut",
                        motkonto_id=motkonto, automatisk=True)
                    messages.info(request, "Regeln sparades och används automatiskt nästa gång.")
            elif atgard == "regel":
                p.verifikation = avstamning.bokfor_regel(p, get_object_or_404(Matchningsregel, pk=d["id"]), anv)
            elif atgard == "ignorera":
                p.ignorerad = True
            elif atgard == "aterstall":
                p.ignorerad = False
                p.verifikation = None
            p.save()
        if p.verifikation_id and atgard != "ver":
            messages.success(request, f"Bokfört som {p.verifikation.vernr}.")
    except bok.BokforingsFel as e:
        messages.error(request, f"Kunde inte bokföra: {e}")
    return nasta_eller(request, "bank")


def _felen(form):
    """Formulärets fel som en läsbar rad."""
    delar = []
    for falt, fel in form.errors.items():
        namn = form.fields[falt].label or falt if falt in form.fields else ""
        delar.append((f"{namn}: " if namn else "") + " ".join(fel))
    return "; ".join(delar)


# ---------------------------------------------------------------------- moms

def _perioder(ar, typ):
    if typ == "manad":
        return [(dt.date(ar, m, 1), _manadsslut(ar, m), f"{ar}-{m:02d}") for m in range(1, 13)]
    if typ == "kvartal":
        return [(dt.date(ar, 3 * q - 2, 1), _manadsslut(ar, 3 * q), f"{ar} kvartal {q}") for q in range(1, 5)]
    return [(dt.date(ar, 1, 1), dt.date(ar, 12, 31), str(ar))]


def _manadsslut(ar, m):
    return (dt.date(ar + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))


def moms(request):
    from . import skatteverket

    foretag = Foretag.hamta()
    idag = timezone.localdate()
    ar = int(request.GET.get("ar", idag.year))
    typ = request.GET.get("typ", foretag.momsperiod)
    perioder = _perioder(ar, typ)
    vald = request.GET.get("period")
    if vald is None:
        vald = next((i for i, p in enumerate(perioder) if p[0] <= idag <= p[1]), 0)
        # förvalt: senast avslutade perioden
        vald = max(0, vald - 1) if ar == idag.year and typ != "ar" else vald
    vald = int(vald)
    fran, till, namn = perioder[vald]
    rapport = bok.momsrapport(fran, till)
    avrakning = bok.aktiva_ver(Verifikation.objects.filter(serie="M", text__contains=f"{fran}–{till}")).first()
    rutor = []
    for _rubrik_id, rubrik, nummer in bok.MOMSRUTOR:
        rutor.append({"rubrik": rubrik, "har_varden": any(rapport["rutor"].get(n) for n in nummer), "rader": [
            {"nr": n, "namn": bok.RUTNAMN[n], "belopp": rapport["rutor"].get(n, Decimal(0)),
             "konton": rapport["per_konto"].get(n, {})} for n in nummer]})
    return render(request, "bokforing/moms.html", {
        "rutor": rutor, "ruta49": rapport["rutor"]["49"], "fran": fran, "till": till, "namn": namn,
        "perioder": [(i, p[2]) for i, p in enumerate(perioder)], "vald": vald, "ar": ar, "typ": typ,
        "avrakning": avrakning, "ar_lista": range(idag.year - 3, idag.year + 2),
        "momskonton": bok.momskonto_saldon(fran, till), "deadline": _momsdeadline(till, typ),
        "ruta49_fil": skatteverket.momsrutor(fran, till)["49"]})


def _momsdeadline(till, typ):
    """Ungefärlig deklarationsdag (12:e andra månaden efter perioden, 26:e för helår/kvartal är olika)."""
    if typ == "ar":
        return None
    m = till.month + 2
    return dt.date(till.year + (m > 12), (m - 1) % 12 + 1, 12)


@require_POST
def moms_avrakna(request):
    form = MomsperiodForm(request.POST)
    if not form.is_valid():
        messages.error(request, _felen(form))
        return nasta_eller(request, "moms")
    fran, till = form.cleaned_data["fran"], form.cleaned_data["till"]
    try:
        v = bok.bokfor_momsavrakning(fran, till, till, ANV(request))
        messages.success(request, f"Momsen för perioden är avräknad mot 2650 ({v.vernr}). "
                                  "När Skatteverket drar beloppet: bokför mallen 'Moms dras från skattekontot'.")
    except bok.BokforingsFel as e:
        messages.error(request, str(e))
    return nasta_eller(request, "moms")


# ------------------------------------------------------------------ rapporter

def _period(request):
    idag = timezone.localdate()
    form = PeriodForm(request.GET or None, initial={"fran": idag.replace(month=1, day=1), "till": idag})
    if form.is_valid():
        fran, till = form.cleaned_data["fran"], form.cleaned_data["till"]
    else:
        fran, till = idag.replace(month=1, day=1), idag
    if fran.year != till.year:
        messages.warning(request, "Perioden bör ligga inom ett räkenskapsår.")
    return form, fran, till


KLASSER = OrderedDict([
    (1, "Tillgångar"), (2, "Eget kapital och skulder"), (3, "Rörelsens intäkter"),
    (4, "Varor, material och tjänster"), (5, "Övriga externa kostnader (lokal, förbrukning, resor)"),
    (6, "Övriga externa kostnader (kontor, tele, tjänster)"), (7, "Personal och avskrivningar"),
    (8, "Finansiella poster, bokslutsdispositioner och skatt"),
])


def rapporter(request):
    form, fran, till = _period(request)
    s = bok.saldon(fran, till)
    namn = dict(Konto.objects.filter(nummer__in=s.keys()).values_list("nummer", "namn"))
    grupper = OrderedDict()
    for k, v in s.items():
        if not (v["ib"] or v["period"]):
            continue
        klass = k // 1000
        g = grupper.setdefault(klass, {"namn": KLASSER.get(klass, ""), "konton": [], "ib": Decimal(0),
                                       "period": Decimal(0), "ub": Decimal(0)})
        g["konton"].append({"nr": k, "namn": namn.get(k, ""), **v})
        for f in ("ib", "period", "ub"):
            g[f] += v[f]
    resultat = -sum((g["period"] for k, g in grupper.items() if k >= 3), Decimal(0))
    intakter = -grupper[3]["period"] if 3 in grupper else Decimal(0)
    tillgangar = grupper[1]["ub"] if 1 in grupper else Decimal(0)
    skulder_ek = grupper[2]["ub"] if 2 in grupper else Decimal(0)
    return render(request, "bokforing/rapporter.html", {
        "form": form, "fran": fran, "till": till, "grupper": grupper, "resultat": resultat,
        "intakter": intakter, "tillgangar": tillgangar, "skulder_ek": skulder_ek,
        "balansdiff": tillgangar + skulder_ek + (-resultat),
        "har_ib": IngaendeBalans.objects.filter(ar=fran.year).exists()})


def huvudbok(request):
    form, fran, till = _period(request)
    konto_q = request.GET.get("konto", "").strip()
    s = bok.saldon(fran, till)
    trans = Transaktion.objects.filter(verifikation__datum__range=(fran, till)).select_related(
        "verifikation").order_by("konto_id", "verifikation__datum", "verifikation__serie", "verifikation__nummer")
    if konto_q:
        try:
            if "-" in konto_q:
                a, b = (int(x) for x in konto_q.split("-"))
            else:
                a = b = int(konto_q)
            trans = trans.filter(konto__nummer__range=(a, b))
            s = {k: v for k, v in s.items() if a <= k <= b}
        except ValueError:
            messages.error(request, "Ange konto som t.ex. 1930 eller 3000-3999.")
    per_konto = defaultdict(list)
    for t in trans:
        per_konto[t.konto_id].append(t)
    namn = dict(Konto.objects.filter(nummer__in=s.keys()).values_list("nummer", "namn"))
    konton = []
    for k, v in s.items():
        saldo = v["ib"]
        rader = []
        for t in per_konto.get(k, []):
            saldo += t.belopp
            rader.append({"t": t, "saldo": saldo})
        if rader or v["ib"]:
            konton.append({"nr": k, "namn": namn.get(k, ""), "ib": v["ib"], "ub": v["ub"], "rader": rader,
                           "debet": sum((t.belopp for t in per_konto.get(k, []) if t.belopp > 0), Decimal(0)),
                           "kredit": -sum((t.belopp for t in per_konto.get(k, []) if t.belopp < 0), Decimal(0))})
    return render(request, "bokforing/huvudbok.html", {"form": form, "konton": konton, "konto_q": konto_q,
                                                       "fran": fran, "till": till})


# ---------------------------------------------------------------- SIE in/ut

def sie_sida(request):
    idag = timezone.localdate()
    export_form = SieExportForm(request.GET if "exportera" in request.GET else None,
                                initial={"fran": idag.replace(month=1, day=1), "till": idag})
    import_form = SieImportForm()
    if "exportera" in request.GET and export_form.is_valid():
        d = export_form.cleaned_data
        qs = Verifikation.objects.filter(datum__range=(d["fran"], d["till"])).order_by("datum", "serie", "nummer")
        if d["serier"]:
            qs = qs.filter(serie__in=d["serier"])
        if d["endast_nya"]:
            qs = qs.filter(sie_exporterad__isnull=True)
        innehall, antal = sie.exportera(qs, Foretag.hamta())
        if not antal:
            messages.info(request, "Inga verifikationer att exportera för urvalet.")
            return redirect("sie")
        nu = timezone.now()
        for v in qs:
            v.sie_exporterad = nu
            v.save(update_fields=["sie_exporterad"])
        resp = HttpResponse(innehall, content_type="application/octet-stream")
        resp["Content-Disposition"] = f'attachment; filename="bitero_{d["fran"]:%Y%m%d}_{d["till"]:%Y%m%d}.se"'
        return resp
    if request.method == "POST":
        import_form = SieImportForm(request.POST, request.FILES)
        if import_form.is_valid():
            try:
                ar, antal = sie.importera_ib(import_form.cleaned_data["fil"].read())
                messages.success(request, f"Ingående balanser för {ar} importerades ({antal} konton).")
                return redirect("sie")
            except Exception as e:
                import_form.add_error("fil", f"Kunde inte läsa filen: {e}")
    ib = [{"ar": r["ar"], "s": bok._d(r["s"])}
          for r in IngaendeBalans.objects.values("ar").annotate(s=Sum("belopp")).order_by("-ar")]
    ej_exp = Verifikation.objects.filter(sie_exporterad__isnull=True).count()
    return render(request, "bokforing/sie.html", {"export_form": export_form, "import_form": import_form,
                                                  "ib": ib, "ej_exp": ej_exp})


def bokforing_start(request):
    idag = timezone.localdate()
    fran = idag.replace(month=1, day=1)
    s = bok.saldon(fran, idag, konton=[1510, 1630, 1930, 2440, 2650])
    resultat = -sum(v["period"] for k, v in bok.saldon(fran, idag).items() if k >= 3000)
    return render(request, "bokforing/start.html", {
        "saldo": {k: v["ub"] for k, v in s.items()}, "resultat": resultat,
        "bank_att_hantera": bok.ofakturerade_bankposter().count(),
        "lev_obetalda": Leverantorsfaktura.objects.filter(betaldatum__isnull=True, typ="faktura").count(),
        "ej_bokforda": Faktura.objects.filter(status__in=["skickad", "betald"], verifikationer__isnull=True,
                                             fran_visma=False).count(),
        "ej_exp": Verifikation.objects.filter(sie_exporterad__isnull=True).count(),
        "senaste": Verifikation.objects.prefetch_related("transaktioner")[:10],
        "mallar": bok.MALLAR, "ar": idag.year})


# ---------------------------------------------------------------- årsarkiv

def arsarkiv(request):
    from django.conf import settings

    from . import arsarkiv as arkiv

    ar_lista = sorted(set(Verifikation.objects.values_list("ar", flat=True)), reverse=True) or \
        [timezone.localdate().year]
    if request.method == "POST":
        try:
            ar = int(request.POST.get("ar", ""))
        except ValueError:
            raise Http404
        if ar not in ar_lista:
            raise Http404
        filnamn, data, info = arkiv.skapa(ar)
        from config.kryptering import KrypteringsFel
        try:
            arkiv.spara_kopia(filnamn, data)
        except KrypteringsFel as e:
            messages.warning(request, f"Ingen kopia sparades i OneDrive: {e}")
        svar = HttpResponse(data, content_type="application/zip")
        svar["Content-Disposition"] = f'attachment; filename="{filnamn}"'
        return svar
    mapp = settings.BACKUP_DIR / "arsarkiv"
    sparade = sorted(mapp.glob("arsarkiv_*.zip*"), reverse=True)[:20] if mapp.exists() else []
    return render(request, "bokforing/arsarkiv.html", {
        "ar_lista": ar_lista, "forvalt": timezone.localdate().year - 1 if timezone.localdate().month < 7
        else timezone.localdate().year, "sparade": [(f.name, f.stat().st_size) for f in sparade],
        "mapp": mapp})


def moms_fil(request):
    """Momsdeklarationen som eSKD-fil att ladda upp hos Skatteverket."""
    from . import skatteverket

    form = MomsperiodForm(request.GET)
    if not form.is_valid():
        messages.error(request, _felen(form))
        return redirect("moms")
    try:
        filnamn, data = skatteverket.momsfil(form.cleaned_data["fran"], form.cleaned_data["till"], Foretag.hamta())
    except skatteverket.DeklarationsFel as e:
        messages.error(request, str(e))
        return nasta_eller(request, "moms")
    svar = HttpResponse(data, content_type="application/xml; charset=iso-8859-1")
    svar["Content-Disposition"] = f'attachment; filename="{filnamn}"'
    return svar


# ---------------------------------------------------------------- bokslut

def bokslutssida(request):
    from . import bokslut
    from .forms import PeriodiseringForm

    idag = timezone.localdate()
    try:
        ar = int(request.GET.get("ar") or request.POST.get("ar") or (idag.year - 1 if idag.month <= 6 else idag.year))
    except ValueError:
        raise Http404
    pform = PeriodiseringForm(request.POST if request.POST.get("atgard") == "periodisering" else None,
                              initial={"typ": "upplupen_intakt", "konto": 3041,
                                       "belopp": bokslut.upparbetat_ej_fakturerat(ar) or None})
    if request.method == "POST":
        atgard, anv = request.POST.get("atgard"), ANV(request)
        try:
            if atgard == "periodisering":
                if not pform.is_valid():
                    raise bok.BokforingsFel(_felen(pform))
                d = pform.cleaned_data
                ver, ater = bokslut.bokfor_periodisering(ar, d["typ"], d["konto"].pk, d["belopp"], d["text"], anv)
                messages.success(request, f"Periodiseringen bokfördes i {ver.vernr} och återförs {ater.datum} i {ater.vernr}.")
            elif atgard == "avskrivningar":
                ver = bokslut.bokfor_avskrivningar(ar, anv)
                messages.success(request, f"Avskrivningarna bokfördes i {ver.vernr}.")
            elif atgard == "skatt":
                ver = bokslut.bokfor_skatt(ar, bokslut.skatteberakning(ar)["skatt"], anv)
                messages.success(request, f"Skatten bokfördes i {ver.vernr}.")
            elif atgard == "resultat":
                ver = bokslut.bokfor_arets_resultat(ar, anv)
                messages.success(request, f"Årets resultat bokfördes i {ver.vernr}.")
            return redirect(f"{request.path}?ar={ar}")
        except bok.BokforingsFel as e:
            messages.error(request, str(e))
            if atgard != "periodisering":
                return redirect(f"{request.path}?ar={ar}")
    ar_lista = sorted(set(Verifikation.objects.values_list("ar", flat=True)) | {idag.year}, reverse=True)
    periodiseringar = Verifikation.objects.filter(ar=ar, text__startswith=f"Bokslut {ar}:", rattar__isnull=True,
                                                  rattelser__isnull=True).order_by("nummer")
    return render(request, "bokforing/bokslut.html", {
        "ar": ar, "ar_lista": ar_lista, "checklista": bokslut.checklista(ar), "pform": pform,
        "upparbetat": bokslut.upparbetat_ej_fakturerat(ar), "avskrivningar": bokslut.avskrivningar(ar),
        "skatt": bokslut.skatteberakning(ar), "ink2r": bokslut.ink2r(ar), "ink2s": bokslut.ink2s(ar),
        "bokslutsver": periodiseringar, "konton": Konto.objects.filter(aktiv=True, nummer__gte=3000, nummer__lt=9000),
    })


@require_POST
def levfaktura_tolka(request):
    """Läser av en PDF och returnerar förslag som JSON (fylls i formuläret av sidan)."""
    from django.http import JsonResponse

    from . import kvitto

    fil = request.FILES.get("fil")
    if not fil:
        return JsonResponse({"fel": "Ingen fil."}, status=400)
    if not fil.name.lower().endswith(".pdf"):
        return JsonResponse({"fel": "Bara PDF-filer kan läsas av automatiskt. Fyll i uppgifterna från bilden själv."})
    try:
        res = kvitto.tolka(fil.read(), Leverantor.objects.filter(aktiv=True), Foretag.hamta().orgnr)
    except kvitto.TolkningsFel as e:
        return JsonResponse({"fel": str(e)})
    lev = res.get("leverantor")
    svar = {k: (str(v) if v is not None else "") for k, v in res.items()
            if k in ("fakturadatum", "forfallodatum", "fakturanummer", "ocr", "belopp", "moms", "momssats",
                     "bankgiro", "orgnr")}
    for k in ("belopp", "moms"):
        if svar.get(k):
            svar[k] = svar[k].replace(".", ",")
    svar["leverantor"] = lev.pk if lev else ""
    svar["leverantor_namn"] = lev.namn if lev else ""
    svar["hittat"] = res["hittat"]
    return JsonResponse(svar)
