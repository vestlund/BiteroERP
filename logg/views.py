import csv
import datetime as dt

from django.apps import apps
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from .models import Handelse, Systemversion
from .sparning import kontrollera_kedjan


def _modellnamn(nyckel):
    try:
        return apps.get_model(nyckel)._meta.verbose_name.capitalize()
    except (LookupError, ValueError):
        return nyckel


def _filtrera(request):
    qs = Handelse.objects.all()
    g = request.GET
    if g.get("modell"):
        qs = qs.filter(modell=g["modell"])
    if g.get("objekt"):
        qs = qs.filter(objekt_id=g["objekt"])
    if g.get("handling"):
        qs = qs.filter(handling=g["handling"])
    if g.get("anvandare"):
        qs = qs.filter(anvandare=g["anvandare"])
    for falt, op in (("fran", "gte"), ("till", "lte")):
        try:
            qs = qs.filter(**{f"tid__date__{op}": dt.date.fromisoformat(g.get(falt, ""))})
        except ValueError:
            pass
    if g.get("q"):
        q = g["q"]
        qs = qs.filter(Q(objekt__icontains=q) | Q(andringar__icontains=q) | Q(kalla__icontains=q))
    return qs


def lista(request):
    qs = _filtrera(request)
    modeller = sorted(Handelse.objects.values_list("modell", flat=True).distinct())
    anvandare = sorted(a for a in Handelse.objects.values_list("anvandare", flat=True).distinct() if a)
    kontroll = kontrollera_kedjan() if request.GET.get("kontrollera") else None
    return render(request, "logg/lista.html", {
        "sida": Paginator(qs, 100).get_page(request.GET.get("sida")),
        "modeller": [(m, _modellnamn(m)) for m in modeller], "anvandare_lista": anvandare,
        "handlingar": Handelse.HANDLINGAR, "g": request.GET, "kontroll": kontroll,
        "namn": {m: _modellnamn(m) for m in modeller},
        "systemversioner": Systemversion.objects.all()[:20], "totalt": Handelse.objects.count(),
    })


def _visa(v):
    if v is None or v == "":
        return "–"
    if v is True:
        return "Ja"
    if v is False:
        return "Nej"
    return v


def _faltnamn(nyckel):
    """{'kund_id': 'Kund', ...} med fältens svenska namn."""
    try:
        modell = apps.get_model(nyckel)
    except (LookupError, ValueError):
        return {}
    return {f.attname: str(f.verbose_name).capitalize() for f in modell._meta.concrete_fields}


def handelse(request, pk):
    h = get_object_or_404(Handelse, pk=pk)
    andringar = h.andringar if isinstance(h.andringar, dict) else {}
    diff = h.handling == Handelse.ANDRAD
    namn = _faltnamn(h.modell)
    rader = []
    for falt, v in andringar.items():
        if diff and isinstance(v, list) and len(v) == 2:
            v = [_visa(v[0]), _visa(v[1])]
        elif not diff:
            v = _visa(v)
        rader.append((namn.get(falt, falt), v))
    return render(request, "logg/handelse.html", {
        "h": h, "modellnamn": _modellnamn(h.modell), "diff": diff, "andringar": sorted(rader),
        "ok_hash": h.berakna_hash() == h.hash,
    })


def systemversion(request, pk):
    return render(request, "logg/systemversion.html", {"sv": get_object_or_404(Systemversion, pk=pk)})


def export_csv(request):
    svar = HttpResponse(content_type="text/csv; charset=utf-8")
    svar["Content-Disposition"] = f'attachment; filename="behandlingshistorik_{dt.date.today():%Y%m%d}.csv"'
    svar.write("﻿")
    w = csv.writer(svar, delimiter=";")
    w.writerow(["Id", "Tid", "Användare", "Källa", "Händelse", "Typ", "Objekt-id", "Objekt", "Ändringar",
                "Föregående hash", "Hash"])
    for h in _filtrera(request).order_by("id").iterator(chunk_size=2000):
        w.writerow([h.pk, h.tid.isoformat(), h.anvandare, h.kalla, h.get_handling_display(), h.modell,
                    h.objekt_id, h.objekt, h.andringar, h.foregaende, h.hash])
    return svar


def systemdokumentation(request):
    import markdown
    from django.conf import settings
    from django.utils.safestring import mark_safe

    from config.version import VERSION

    delar = []
    for namn in ("systemdokumentation.md", "andringslogg.md"):
        fil = settings.BASE_DIR / "docs" / namn
        if fil.exists():
            delar.append(markdown.markdown(fil.read_text(encoding="utf-8"), extensions=["tables", "toc"]))
    senaste = Systemversion.objects.first()
    return render(request, "logg/systemdokumentation.html", {
        "innehall": mark_safe('<hr id="andringslogg">'.join(delar)), "version": VERSION, "senaste": senaste})
