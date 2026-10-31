import datetime as dt

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from register.models import Foretag

from . import frister as fr
from . import likviditet


def _f(v):
    return float(v or 0)


def likviditetssida(request):
    try:
        veckor = max(4, min(52, int(request.GET.get("veckor", 13))))
    except ValueError:
        veckor = 13
    lp = likviditet.prognos(veckor=veckor)
    diagram = [{"etikett": f"v{v['vecka']}", "varde": _f(v["saldo"]),
                "detalj": "".join(f"<div>{k}: {_f(b):,.0f} kr</div>".replace(",", " ")
                                  for k, b in v["kategorier"].items())} for v in lp["veckor"]]
    return render(request, "planering/likviditet.html", {"lp": lp, "diagram": diagram, "veckor": veckor})


def _har_lon(idag):
    from lon.models import Anstalld
    return Anstalld.objects.filter(aktiv=True).exists() or likviditet.lonemonster(idag) is not None


def fristsida(request):
    foretag = Foretag.hamta()
    idag = timezone.localdate()
    lista = fr.frister(idag - dt.timedelta(days=31), idag + dt.timedelta(days=400), foretag, _har_lon(idag))
    for f in lista:
        f["passerad"] = f["datum"] < idag
        f["dagar"] = (f["datum"] - idag).days
    return render(request, "planering/frister.html", {"frister": lista, "idag": idag})


def frister_ics(request):
    foretag = Foretag.hamta()
    idag = timezone.localdate()
    lista = fr.frister(idag - dt.timedelta(days=31), idag + dt.timedelta(days=730), foretag, _har_lon(idag))
    svar = HttpResponse(fr.ics(lista, foretag), content_type="text/calendar; charset=utf-8")
    svar["Content-Disposition"] = 'attachment; filename="frister.ics"'
    return svar


def skattesida(request):
    from decimal import Decimal, InvalidOperation

    from . import skatt
    idag = timezone.localdate()
    foretag = Foretag.hamta()
    if request.method == "POST":
        from django.contrib import messages
        from django.shortcuts import redirect
        try:
            foretag.fskatt_manad = Decimal(request.POST["fskatt_manad"].replace(" ", "").replace(",", ".")) \
                if request.POST.get("fskatt_manad", "").strip() else None
            foretag.agarandel = Decimal(request.POST.get("agarandel", "100").replace(",", "."))
            foretag.sparat_utdelningsutrymme = Decimal(
                (request.POST.get("sparat") or "0").replace(" ", "").replace(",", "."))
            if not (0 < foretag.agarandel <= 100):
                raise InvalidOperation
            foretag.save(update_fields=["fskatt_manad", "agarandel", "sparat_utdelningsutrymme"])
            messages.success(request, "Uppgifterna sparades.")
        except (InvalidOperation, KeyError):
            messages.error(request, "Kontrollera beloppen (ägarandel 1–100 %).")
        return redirect("skatt")
    egen_lon = None
    if request.GET.get("egen_lon"):
        try:
            egen_lon = Decimal(request.GET["egen_lon"].replace(" ", "").replace(",", "."))
        except InvalidOperation:
            egen_lon = None
    return render(request, "planering/skatt.html", {
        "prognos": skatt.skatteprognos(idag), "utd": skatt.utdelning(idag, egen_lon=egen_lon),
        "foretag": foretag, "egen_lon": egen_lon, "idag": idag})
