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
            foretag.save(update_fields=["kommunalskatt", "begravningsavgift", "max_uttag", "max_uttag_inkl_avgifter",
                                        "agarandel"])
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
             "kommunalskatt": foretag.kommunalskatt,
             "begravningsavgift": foretag.begravningsavgift,
             "arbetsgivaravgift": lu.agarens_arbetsgivaravgift(idag.year)}
    # Lönenivåerna för full SGI och pension beror inte på skatt eller avgift, bara på årets basbelopp
    standard = lu.rekommenderad_lon(idag.year, start["arbetsgivaravgift"])
    nivaer = {"sgi": standard["sgi"]["lon"], "pension": standard["pension"]["lon"]}
    form = LonUtdelningForm(request.GET if "resultat" in request.GET else None, initial=start, nivaer=nivaer)
    data = form.cleaned_data if form.is_bound and form.is_valid() else (start if not form.is_bound else None)
    resultat = None
    if data:
        f = lu.Forutsattningar(
            ar=idag.year, resultat_fore_agarlon=Decimal(data["resultat"]),
            kommunalskatt=Decimal(data["kommunalskatt"]), avgift=Decimal(data.get("begravningsavgift") or 0),
            arbetsgivaravgift=Decimal(data["arbetsgivaravgift"]), agarandel=Decimal(data["agarandel"]),
            ovriga_loner=Decimal(data.get("ovriga_loner") or 0),
            fritt_eget_kapital=Decimal(data.get("fritt_eget_kapital") or 0),
            max_uttag=Decimal(data["max_uttag"]) if data.get("max_uttag") is not None else None,
            max_inklusive_avgifter=bool(data.get("max_inkl_avgifter")), min_lon=Decimal(data.get("min_lon") or 0))
        bast, alla = lu.optimera(f)
        rek_f = lu.rekommenderad_lon(idag.year, f.arbetsgivaravgift, f.kommunalskatt, f.avgift)
        markeringar = [{"lon": rek_f["pension"]["lon"], "text": "Full pension"},
                       {"lon": rek_f["sgi"]["lon"], "text": "Full SGI"}]
        resultat = {"bast": bast, "tabell": lu.jamforelse(f, bast, alla), "f": f,
                    "diagram": _diagram(alla, bast, markeringar) if alla else None}
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
    q.pop("min_lon_niva", None)
    return render(request, "planering/lon_och_utdelning.html", {
        "form": form, "nivaer": {k: str(v) for k, v in nivaer.items()}, "r": resultat, "prognos": prognos, "idag": idag, "rek": rek,
        "med_minimilon": q.urlencode(), "fritt_fg": fritt_eget_kapital(idag.year - 1)})


def _snygga_steg(spann, antal=5):
    """Ett "jämnt" steg (1, 2, 2,5 eller 5 × 10^n) som ger ungefär `antal` skalsteg."""
    import math
    if spann <= 0:
        return 1
    rå = spann / antal
    tiopot = 10 ** math.floor(math.log10(rå))
    for f in (1, 2, 2.5, 5, 10):
        if rå <= f * tiopot:
            return f * tiopot
    return 10 * tiopot


def _tkr(v):
    """Skaletikett i tusental kronor: 850 000 -> '850 tkr', 1 200 000 -> '1 200 tkr'."""
    return f"{round(v / 1000):,}".replace(",", " ") + " tkr"


def _diagram(alla, bast, markeringar=(), bredd=760, hojd=320):
    """Linjediagram: netto till ägaren (y) som funktion av lön per år (x), med skalor och hjälplinjer.

    Alla koordinater lämnas som text så att mallen inte lokaliserar dem (decimalkomma i SVG fungerar inte).
    """
    import math

    vanster, hoger, topp, botten = 72, 20, 18, 52
    pb, ph = bredd - vanster - hoger, hojd - topp - botten
    steg = max(1, len(alla) // 200)
    urval = alla[::steg] + ([alla[-1]] if alla[-1] not in alla[::steg] else [])
    xs = [float(a["lon"]) for a in urval]
    ys = [float(a["netto"]) for a in urval]

    xsteg = _snygga_steg(max(xs) or 1, antal=6)
    xmax = math.ceil((max(xs) or 1) / xsteg) * xsteg
    ysteg = _snygga_steg((max(ys) - min(ys)) or max(ys) or 1)
    ymin = math.floor(min(ys) / ysteg) * ysteg
    ymax = math.ceil(max(ys) / ysteg) * ysteg
    if ymax == ymin:
        ymax = ymin + ysteg

    def x(v):
        return vanster + pb * v / xmax

    def y(v):
        return topp + ph * (1 - (v - ymin) / (ymax - ymin))

    def f(v):
        return f"{v:.1f}"

    xticks = [{"x": f(x(v)), "text": _tkr(v)} for v in _intervall(0, xmax, xsteg)]
    yticks = [{"y": f(y(v)), "text": _tkr(v)} for v in _intervall(ymin, ymax, ysteg)]
    # Hjälplinjer sorteras på lön; den lägre får texten till vänster om linjen, den högre till höger,
    # så att närliggande linjer (SGI och pension) inte krockar.
    synliga = sorted((m for m in markeringar if 0 < float(m["lon"]) <= xmax), key=lambda m: float(m["lon"]))
    linjer = [{"x": f(x(float(m["lon"]))), "text": m["text"],
               "ankare": "end" if i < len(synliga) - 1 else "start", "dx": "-4" if i < len(synliga) - 1 else "4"}
              for i, m in enumerate(synliga)]
    bx, by = x(float(bast["lon"])), y(float(bast["netto"]))
    punkter = [[round(x(a), 1), round(y(b), 1), int(a), int(b), int(u["utdelning"])]
               for a, b, u in zip(xs, ys, urval)]
    return {
        "bredd": str(bredd), "hojd": str(hojd), "vanster": str(vanster), "hoger_x": f(vanster + pb),
        "topp": str(topp), "botten_y": f(topp + ph), "mitt_y": f(topp + ph / 2), "mitt_x": f(vanster + pb / 2),
        "xtitel_y": f(hojd - 8), "linje": " ".join(f"{p[0]},{p[1]}" for p in punkter),
        "xticks": xticks, "yticks": yticks, "markeringar": linjer, "bast_x": f(bx), "bast_y": f(by),
        "bast_text_x": f(min(bx + 8, vanster + pb - 4)), "bast_text_y": f(max(by - 10, topp + 12)),
        "bast_ankare": "end" if bx > vanster + pb * 0.7 else "start",
        "bredd_plot": f(pb), "hojd_plot": f(ph), "punkter": punkter,
    }


def _intervall(fran, till, steg):
    v, ut = fran, []
    while v <= till + steg / 1000:
        ut.append(v)
        v += steg
    return ut
