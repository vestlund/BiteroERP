import datetime as dt
from decimal import Decimal

from django.contrib import messages
from django.http import HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
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
    from django.contrib import messages
    from django.shortcuts import redirect

    from . import skatt
    from .forms import EgenLonForm, SkatteuppgifterForm
    idag = timezone.localdate()
    foretag = Foretag.hamta()
    form = SkatteuppgifterForm(request.POST or None, instance=foretag)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "Uppgifterna sparades.")
            return redirect("skatt")
        messages.error(request, "Kontrollera uppgifterna (ägarandel 1–100 %, belopp noll eller mer).")
    lonform = EgenLonForm(request.GET or None)
    egen_lon = lonform.cleaned_data["egen_lon"] if lonform.is_bound and lonform.is_valid() else None
    return render(request, "planering/skatt.html", {
        "prognos": skatt.skatteprognos(idag), "utd": skatt.utdelning(idag, egen_lon=egen_lon),
        "form": form, "lonform": lonform,
        "foretag": foretag, "egen_lon": egen_lon, "idag": idag})


def lon_och_utdelning(request):
    """Räkna på fördelningen mellan lön och utdelning som ger störst nettobelopp (3:12-reglerna)."""
    from planering import lon_utdelning as lu
    from planering.skatt import fritt_eget_kapital

    from .forms import LonUtdelningForm

    foretag = Foretag.hamta()
    idag = timezone.localdate()
    if request.method == "POST":  # spara skatteuppgifter och maxbelopp som standard
        form = LonUtdelningForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            foretag.kommunalskatt = d["kommunalskatt"]
            foretag.begravningsavgift = d["begravningsavgift"] or 0
            foretag.max_uttag = d["max_uttag"]
            foretag.max_uttag_inkl_avgifter = d["max_inkl_avgifter"]
            foretag.agarandel = d["agarandel"]
            foretag.sparat_utdelningsutrymme = d["sparat"] or 0
            foretag.save(update_fields=["kommunalskatt", "begravningsavgift", "max_uttag", "max_uttag_inkl_avgifter",
                                        "agarandel", "sparat_utdelningsutrymme"])
            messages.success(request, "Uppgifterna sparades och används som utgångsläge nästa gång.")
        else:
            messages.error(request, "Kontrollera uppgifterna.")
        q = request.POST.copy()
        for nyckel in ("csrfmiddlewaretoken", "spara"):
            q.pop(nyckel, None)
        return redirect(f"{reverse('lon_och_utdelning')}?{q.urlencode()}")

    prognos = lu.resultat_fore_lon(idag)
    start = {"resultat": prognos["resultat"], "max_uttag": foretag.max_uttag,
             "max_inkl_avgifter": foretag.max_uttag_inkl_avgifter, "agarandel": foretag.agarandel,
             "sparat": foretag.sparat_utdelningsutrymme, "kommunalskatt": foretag.kommunalskatt,
             "begravningsavgift": foretag.begravningsavgift,
             "arbetsgivaravgift": lu.agarens_arbetsgivaravgift(idag.year)}
    form = LonUtdelningForm(request.GET if "resultat" in request.GET else None, initial=start)
    data = form.cleaned_data if form.is_bound and form.is_valid() else (start if not form.is_bound else None)
    resultat = None
    if data:
        f = lu.Forutsattningar(
            ar=idag.year, resultat_fore_agarlon=Decimal(data["resultat"]),
            kommunalskatt=Decimal(data["kommunalskatt"]), avgift=Decimal(data.get("begravningsavgift") or 0),
            arbetsgivaravgift=Decimal(data["arbetsgivaravgift"]), agarandel=Decimal(data["agarandel"]),
            ovriga_loner=Decimal(data.get("ovriga_loner") or 0), sparat=Decimal(data.get("sparat") or 0),
            fritt_eget_kapital=Decimal(data.get("fritt_eget_kapital") or 0),
            max_uttag=Decimal(data["max_uttag"]) if data.get("max_uttag") is not None else None,
            max_inklusive_avgifter=bool(data.get("max_inkl_avgifter")), min_lon=Decimal(data.get("min_lon") or 0),
            bara_inom_gransbelopp=bool(data.get("bara_inom_gransbelopp")))
        bast, alla = lu.optimera(f)
        resultat = {"bast": bast, "tabell": lu.jamforelse(f, bast, alla), "f": f,
                    "diagram": _diagram(alla, bast) if alla else None}
    # Rekommenderad lägsta lön (full SGI och allmän pension), med samma avgift och skatt som kalkylen
    grund = data or start
    rek = lu.rekommenderad_lon(idag.year, Decimal(grund["arbetsgivaravgift"]), Decimal(grund["kommunalskatt"]),
                               Decimal(grund.get("begravningsavgift") or 0))
    q = request.GET.copy() if form.is_bound else QueryDict(mutable=True)
    if not form.is_bound:
        for k, v in start.items():
            if v is not None and v is not False:
                q[k] = "on" if v is True else str(v)
    q["min_lon"] = str(rek["rekommenderad"]["lon"])
    return render(request, "planering/lon_och_utdelning.html", {
        "form": form, "r": resultat, "prognos": prognos, "idag": idag, "rek": rek,
        "med_minimilon": q.urlencode(), "fritt_fg": fritt_eget_kapital(idag.year - 1)})


def _diagram(alla, bast, bredd=640, hojd=220):
    """Punkter för ett enkelt linjediagram: netto till ägaren som funktion av lönen."""
    steg = max(1, len(alla) // 120)
    urval = alla[::steg] + ([alla[-1]] if alla[-1] not in alla[::steg] else [])
    xmax = max(float(a["lon"]) for a in urval) or 1
    ymin = min(float(a["netto"]) for a in urval)
    ymax = max(float(a["netto"]) for a in urval)
    yspann = (ymax - ymin) or 1

    def xy(a):
        return (round(40 + (bredd - 60) * float(a["lon"]) / xmax, 1),
                round(10 + (hojd - 40) * (1 - (float(a["netto"]) - ymin) / yspann), 1))
    bx, by = xy(bast)
    return {"bredd": bredd, "hojd": hojd, "linje": " ".join(f"{x},{y}" for x, y in map(xy, urval)),
            "bast_x": f"{bx}", "bast_y": f"{by}", "xmax": Decimal(xmax), "ymin": Decimal(ymin), "ymax": Decimal(ymax)}
