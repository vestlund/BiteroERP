from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from register.models import Konto
from verksamhet.models import Uppdrag

from . import tjanster
from .forms import BudgetHuvudForm, BudgetNyForm, BudgetradForm
from .models import Budget, Budgetrad

MANADER = ["Jan", "Feb", "Mar", "Apr", "Maj", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]


def lista(request):
    return render(request, "budget/lista.html", {"budgetar": Budget.objects.prefetch_related("rader")})


def ny(request):
    idag = timezone.localdate()
    if request.method == "POST":
        form = BudgetNyForm(request.POST)
        if not form.is_valid():
            messages.error(request, _felen(form))
            return redirect("budget_ny")
        d = form.cleaned_data
        with transaction.atomic():
            b = Budget.objects.create(ar=d["ar"], namn=d["namn"], huvudbudget=d["huvudbudget"])
            if d["kalla"] == "forslag":
                tjanster.skapa_forslag(b, idag, d["belaggning"] or Decimal("90"), d["timmar"] or Decimal("8"))
            elif d["kalla"]:
                tjanster.kopiera(d["kalla"], b)
        messages.success(request, f"{b} skapades. Justera raderna och spara.")
        return redirect("budget_redigera", pk=b.pk)
    return render(request, "budget/ny.html", {
        "ar_val": [idag.year, idag.year + 1, idag.year + 2], "forslag_ar": BudgetNyForm.standardar(),
        "budgetar": Budget.objects.all(), "idag": idag})


def _felen(form, rubrik=""):
    delar = []
    for falt, fel in form.errors.items():
        namn = str(form.fields[falt].label or falt) if falt in form.fields else ""
        delar.append((f"{rubrik}{namn}: " if namn or rubrik else "") + " ".join(fel))
    return "; ".join(delar)


def _radformular(data):
    """Ett formulär per rad i rutnätet, i den ordning raderna står."""
    nycklar = {k.split("-")[1] for k in data if k.startswith("r-") and k.endswith("-typ")}
    formular = [BudgetradForm(data, prefix=f"r-{n}") for n in nycklar]
    giltiga = all(f.is_valid() for f in formular)
    formular.sort(key=lambda f: f.cleaned_data.get("ordning") or 0 if giltiga else 0)
    return formular, giltiga


def redigera(request, pk):
    b = get_object_or_404(Budget, pk=pk)
    if request.method == "POST":
        huvud = BudgetHuvudForm(request.POST, instance=b)
        rader, giltiga = _radformular(request.POST)
        if not huvud.is_valid() or not giltiga:
            fel = [_felen(huvud)] if huvud.errors else []
            fel += [_felen(f, f"Rad '{f.data.get(f.prefix + '-beskrivning') or '?'}' ") for f in rader if f.errors]
            messages.error(request, "Budgeten sparades inte. " + "; ".join(fel))
            return redirect("budget_redigera", pk=b.pk)
        with transaction.atomic():
            huvud.save()
            befintliga = {str(r.pk): r for r in b.rader.all()}
            for i, f in enumerate(rader):
                d, n = f.cleaned_data, f.prefix.split("-", 1)[1]
                if d["radera"]:
                    if n in befintliga:
                        befintliga[n].delete()
                    continue
                r = befintliga.get(n) or Budgetrad(budget=b)
                r.typ = d["typ"]
                r.beskrivning = d["beskrivning"] or "–"
                r.uppdrag = d["uppdrag"]
                r.kund = r.uppdrag.kund if r.uppdrag else None
                r.konto = d["konto"]
                if r.typ == Budgetrad.TID and r.uppdrag and r.beskrivning == "–":
                    r.beskrivning = str(r.uppdrag)
                if r.typ == Budgetrad.KOSTNAD and r.konto and r.beskrivning == "–":
                    r.beskrivning = r.konto.namn
                r.timpris = (d["timpris"] or Decimal(0)) if r.typ == Budgetrad.TID else None
                r.varden = f.varden
                r.ordning = i
                if r.pk or r.summa or r.beskrivning != "–":
                    r.save()
        messages.success(request, "Budgeten sparades.")
        return redirect("budget_uppfoljning" if "visa" in request.POST else "budget_redigera", pk=b.pk)
    grupper = [(t, n, [r for r in b.rader.select_related("uppdrag", "konto") if r.typ == t])
               for t, n in Budgetrad.TYPER]
    return render(request, "budget/redigera.html", {
        "b": b, "grupper": grupper, "manader": MANADER, "arbetsdagar": tjanster.arbetsdagar(b.ar),
        "uppdrag": Uppdrag.objects.select_related("kund"),
        "konton": Konto.objects.filter(aktiv=True, nummer__gte=4000, nummer__lt=9000),
        "intaktskonton": Konto.objects.filter(aktiv=True, nummer__gte=3000, nummer__lt=4000),
        "startmanad": timezone.localdate().month if b.ar == timezone.localdate().year else 1,
        "summa": b.per_manad()})


@require_POST
def radera(request, pk):
    b = get_object_or_404(Budget, pk=pk)
    b.delete()
    messages.success(request, "Budgeten raderades.")
    return redirect("budgetar")


def uppfoljning(request, pk=None):
    idag = timezone.localdate()
    if pk:
        b = get_object_or_404(Budget, pk=pk)
    else:
        ar = int(request.GET.get("ar") or idag.year)
        b = Budget.objects.filter(ar=ar, huvudbudget=True).first() or Budget.objects.filter(ar=ar).first()
        if not b:
            return render(request, "budget/ingen.html", {"ar": ar})
    u = tjanster.uppfoljning(b, idag)
    tabell = []
    for i, m in enumerate(MANADER):
        tabell.append({
            "manad": m, "stangd": i < u["stangda"], "aktuell": i == u["stangda"] and b.ar == idag.year,
            "tim_b": u["budget"]["timmar"][i], "tim_u": u["utfall"]["timmar"][i],
            "int_b": u["budget"]["intakter"][i], "upparb": u["utfall"]["upparbetat"][i],
            "fakt": u["utfall"]["intakter"][i], "kost_b": u["budget"]["kostnader"][i],
            "kost_u": u["utfall"]["kostnader"][i], "res_b": u["resultat"]["budget"][i],
            "res_u": u["resultat"]["utfall"][i], "res_p": u["prognos"]["resultat"][i],
        })
    f = float
    diagram = {
        "kategorier": MANADER,
        "int_budget": [f(v) for v in u["budget"]["intakter"]],
        "int_utfall": [f(v) for v in u["intakt_utfall"]],
        "ack_budget": [f(v) for v in u["ack"]["budget"]],
        "ack_utfall": [f(v) for v in u["ack"]["utfall"]],
        "ack_prognos": [f(v) for v in u["ack"]["prognos"]],
    }
    return render(request, "budget/uppfoljning.html", {
        "b": b, "u": u, "tabell": tabell, "diagram": diagram, "s": u["summa"],
        "andra": Budget.objects.exclude(pk=b.pk), "idag": idag})
