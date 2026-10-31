import datetime as dt
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Max
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from bokforing import tjanster as bok

from . import skattetabell
from . import tjanster as lon
from .forms import ANSTALLD_GRUPPER, AnstalldForm, LonekorningForm, LoneradFormSet, LonespecForm, SkattetabellForm
from .models import LONEARTER, Anstalld, Lonekorning, Lonespec

ANV = lambda r: r.user.get_username()  # noqa: E731


# ---------------------------------------------------------------- anställda

def anstallda(request):
    alla = request.GET.get("visa") == "alla"
    qs = Anstalld.objects.all() if alla else Anstalld.objects.filter(aktiv=True)
    return render(request, "lon/anstallda.html", {"anstallda": qs, "alla": alla})


def anstalld_form(request, pk=None):
    obj = get_object_or_404(Anstalld, pk=pk) if pk else None
    initial = {} if obj else {"nummer": (Anstalld.objects.aggregate(m=Max("nummer"))["m"] or 0) + 1,
                              "loneskonto": 7210}
    form = AnstalldForm(request.POST or None, instance=obj, initial=initial)
    if request.method == "POST" and form.is_valid():
        a = form.save()
        messages.success(request, f"{a.namn} sparad.")
        return redirect(a)
    grupper = [(r, [form[f] for f in falt]) for r, falt in ANSTALLD_GRUPPER]
    return render(request, "register/form.html", {
        "cfg": {"titel": "Anställda", "singular": "anställd"}, "typ": None, "obj": obj, "form": form,
        "grupper": grupper, "ingen_radera": True, "tillbaka": ("anstallda", "Anställda")})


def anstalld(request, pk):
    a = get_object_or_404(Anstalld, pk=pk)
    ar = timezone.localdate().year
    specar = a.specar.select_related("korning").prefetch_related("rader").order_by("-korning__utbetalningsdatum")
    ackumulerat = {"brutto": Decimal(0), "skatt": Decimal(0), "formaner": Decimal(0), "avgift": Decimal(0)}
    lista = []
    for s in specar:
        b = s.berakna()
        lista.append((s, b))
        if s.korning.utbetalningsdatum.year == ar and s.korning.last:
            for k in ackumulerat:
                ackumulerat[k] += b[k]
    return render(request, "lon/anstalld.html", {"a": a, "specar": lista, "ack": ackumulerat, "ar": ar})


# ------------------------------------------------------------ lönekörningar

def korningar(request):
    lista = [(k, k.summor()) for k in Lonekorning.objects.prefetch_related("specar__rader", "specar__anstalld")]
    return render(request, "lon/korningar.html", {"korningar": lista,
                                                  "har_anstallda": Anstalld.objects.filter(aktiv=True).exists()})


def korning_ny(request):
    idag = timezone.localdate()
    utb = idag.replace(day=25)
    form = LonekorningForm(request.POST or None, initial={
        "period": f"{idag:%Y-%m}", "utbetalningsdatum": utb,
        "anstallda": Anstalld.objects.filter(aktiv=True)})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            k = form.save()
            for a in form.cleaned_data["anstallda"]:
                spec = Lonespec.objects.create(korning=k, anstalld=a)
                lon.standardrader(spec)
                forra = Lonespec.objects.filter(anstalld=a).exclude(pk=spec.pk).order_by(
                    "-korning__utbetalningsdatum").first()
                if forra:
                    lon.kopiera_fasta_rader(spec, forra)
                lon.uppdatera_skatt(spec)
        messages.success(request, "Lönekörningen är skapad. Kontrollera varje lönespecifikation och bokför.")
        return redirect(k)
    return render(request, "lon/korning_ny.html", {"form": form})


def korning(request, pk):
    k = get_object_or_404(Lonekorning, pk=pk)
    specar = [(s, s.berakna()) for s in k.specar.select_related("anstalld").prefetch_related("rader")]
    saknar_skatt = [s for s, b in specar if b["underlag"] > 0 and not s.skatteavdrag
                    and s.anstalld.skattemetod != s.anstalld.SKATT_INGEN]
    return render(request, "lon/korning.html", {
        "k": k, "specar": specar, "sum": k.summor(), "saknar_skatt": saknar_skatt,
        "vers": k.verifikationer.all()})


@require_POST
def korning_atgard(request, pk, atgard):
    k = get_object_or_404(Lonekorning, pk=pk)
    try:
        if atgard == "bokfor":
            ver = lon.bokfor_lonekorning(k, ANV(request))
            messages.success(request, f"Lönerna är bokförda som {ver.vernr}. Betala ut nettolönerna "
                                      f"{k.utbetalningsdatum} och lämna arbetsgivardeklarationen.")
        elif atgard == "las_upp" and k.last:
            lon.las_upp(k, timezone.localdate(), ANV(request))
            messages.warning(request, "Bokföringen är rättad och lönekörningen kan ändras igen. "
                                      "Har du redan lämnat arbetsgivardeklarationen behöver den rättas.")
        elif atgard == "radera" and not k.last and not k.verifikationer.exists():
            k.delete()
            messages.success(request, "Lönekörningen raderades.")
            return redirect("lonekorningar")
    except bok.BokforingsFel as e:
        messages.error(request, str(e))
    return redirect(k)


def lonespec(request, pk):
    s = get_object_or_404(Lonespec.objects.select_related("anstalld", "korning"), pk=pk)
    las = s.korning.last
    form = LonespecForm(request.POST or None, instance=s, prefix="s")
    rader = LoneradFormSet(request.POST or None, instance=s, prefix="r")
    if request.method == "POST" and not las:
        if form.is_valid() and rader.is_valid():
            with transaction.atomic():
                ny_skatt = form.cleaned_data["skatteavdrag"]
                s = form.save(commit=False)
                rader.save()
                if "foresla" in request.POST:
                    s.skatt_manuell = False
                    s.save()
                    forslag = s.foreslagen_skatt()
                    if forslag is None:
                        messages.warning(request, "Hittar ingen skattetabell för beloppet. Skriv in skatteavdraget "
                                                  "från Skatteverkets tabell.")
                    else:
                        s.skatteavdrag = forslag
                        s.save()
                else:
                    s.skatt_manuell = "skatteavdrag" in form.changed_data or s.skatt_manuell
                    s.save()
                    if not s.skatt_manuell:
                        lon.uppdatera_skatt(s)
            messages.success(request, "Lönespecifikationen sparades.")
            return redirect("lonekorning" if "tillbaka" in request.POST else "lonespec",
                            pk=s.korning_id if "tillbaka" in request.POST else s.pk)
    b = s.berakna()
    return render(request, "lon/lonespec.html", {
        "s": s, "form": form, "rader": rader, "b": b, "las": las, "lonearter": LONEARTER,
        "forslag": s.foreslagen_skatt(), "har_tabell": skattetabell.har_tabell(s.korning.utbetalningsdatum.year)})


def lonespec_utskrift(request, pk):
    k = get_object_or_404(Lonekorning, pk=pk)
    specar = k.specar.select_related("anstalld").prefetch_related("rader")
    if sp := request.GET.get("spec"):
        specar = specar.filter(pk=sp)
    ar = k.utbetalningsdatum.year
    lista = []
    for s in specar:
        ack = {"brutto": Decimal(0), "skatt": Decimal(0)}
        for t in Lonespec.objects.filter(anstalld=s.anstalld, korning__utbetalningsdatum__year=ar,
                                         korning__utbetalningsdatum__lte=k.utbetalningsdatum).prefetch_related("rader"):
            b2 = t.berakna()
            ack["brutto"] += b2["underlag"]
            ack["skatt"] += b2["skatt"]
        lista.append((s, s.berakna(), ack))
    return render(request, "lon/utskrift.html", {"k": k, "specar": lista})


# ---------------------------------------------------------------- AGI

def arbetsgivardeklaration(request):
    idag = timezone.localdate()
    period = request.GET.get("period") or f"{idag:%Y-%m}"
    try:
        ar, man = (int(x) for x in period.split("-"))
        dt.date(ar, man, 1)
    except ValueError:
        ar, man = idag.year, idag.month
        period = f"{idag:%Y-%m}"
    data = lon.agi(period)
    m = man + 1
    deadline = dt.date(ar + (m > 12), (m - 1) % 12 + 1, 17 if m in (1, 8, 13) else 12)
    return render(request, "lon/agi.html", {"period": period, "d": data, "deadline": deadline,
                                            "grupp_namn": {"full": "Full avgift 31,42 %",
                                                           "unga": "Tillfälligt nedsatt 19–23 år (20,81 % upp till 25 000 kr/mån)",
                                                           "aldre": "Endast ålderspensionsavgift 10,21 %",
                                                           "ingen": "Ingen avgift (född 1937 eller tidigare)"}})


def skattetabeller(request):
    form = SkattetabellForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            ar, antal = skattetabell.importera(form.cleaned_data["fil"].read())
            messages.success(request, f"Skattetabeller för {', '.join(map(str, ar))} importerades ({antal} rader).")
            return redirect("skattetabeller")
        except skattetabell.TabellFel as e:
            form.add_error("fil", str(e))
    from django.db.models import Count
    from .models import Skattetabellrad
    finns = Skattetabellrad.objects.values("ar").annotate(n=Count("id"), t=Count("tabell", distinct=True)).order_by("-ar")
    return render(request, "lon/skattetabeller.html", {"form": form, "finns": finns})


def agi_fil(request):
    """Arbetsgivardeklarationen som XML-fil att ladda upp hos Skatteverket."""
    from register.models import Foretag

    from . import agifil

    period = request.GET.get("period", "")
    try:
        filnamn, data, _ = agifil.agi_xml(period, Foretag.hamta())
    except (agifil.AgiFel, ValueError) as e:
        messages.error(request, str(e) or "Ogiltig period.")
        return redirect(f"{reverse('agi')}?period={period}")
    svar = HttpResponse(data, content_type="application/xml; charset=utf-8")
    svar["Content-Disposition"] = f'attachment; filename="{filnamn}"'
    return svar
