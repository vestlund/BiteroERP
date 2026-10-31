"""Egna sidor för grundregistren (ersätter Django-admin i vardagen)."""

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Max, ProtectedError, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from bokforing.models import Inventarie, Matchningsregel
from config.formular import nasta_eller

from .models import Artikel, Betalningsvillkor, Foretag, Konto, Kostnadsstalle, Kund, Leverantor

PART_FALT = [
    ("Grunduppgifter", ["nummer", "namn", "orgnr", "aktiv"]),
    ("Adress", ["adress1", "adress2", "postnummer", "ort", "landkod"]),
    ("Kontakt", ["telefon", "mobil", "epost", "webb", "kontaktperson", "kontakt_epost", "kontakt_telefon",
                 "kontakt_mobil"]),
]

REGISTER = {
    "kunder": {
        "modell": Kund, "titel": "Kunder", "singular": "kund",
        "kolumner": [("nummer", "Nr"), ("namn", "Namn"), ("orgnr", "Org.nr"), ("ort", "Ort"),
                     ("kontaktperson", "Kontakt"), ("betalningsvillkor", "Villkor")],
        "sok": ["namn", "orgnr", "kontaktperson", "epost", "ort"],
        "falt": PART_FALT + [
            ("Fakturering", ["betalningsvillkor", "valuta", "rabatt_procent", "sprak", "momsnummer", "gln",
                             "kopia_epost", "privatperson"]),
            ("Leveransadress", ["leverans_namn", "leverans_adress1", "leverans_adress2", "leverans_postnummer",
                                "leverans_ort"]),
            ("Övrigt", ["etiketter", "anteckning"]),
        ],
        "detalj": lambda o: reverse("kund_detalj", args=[o.pk]),
    },
    "leverantorer": {
        "modell": Leverantor, "titel": "Leverantörer", "singular": "leverantör",
        "kolumner": [("nummer", "Nr"), ("namn", "Namn"), ("orgnr", "Org.nr"), ("bankgiro", "Bankgiro"),
                     ("plusgiro", "Plusgiro"), ("standardkonto", "Konto")],
        "sok": ["namn", "orgnr", "bankgiro", "plusgiro"],
        "falt": PART_FALT + [
            ("Bokföring", ["standardkonto", "standard_momssats", "betalningsvillkor", "valuta"]),
            ("Betalning", ["bankgiro", "plusgiro", "ocr", "autogiro", "bank", "bic", "iban", "bban"]),
            ("Övrigt", ["anteckning"]),
        ],
    },
    "artiklar": {
        "modell": Artikel, "titel": "Artiklar", "singular": "artikel",
        "kolumner": [("nummer", "Nr"), ("namn", "Namn"), ("get_enhet_display", "Enhet"), ("pris", "Pris"),
                     ("intaktskonto", "Konto"), ("momssats", "Moms %")],
        "sok": ["nummer", "namn"],
        "falt": [("Artikel", ["nummer", "namn", "namn_engelska", "aktiv", "enhet", "pris", "inkopspris",
                              "intaktskonto", "momssats", "anteckning"])],
    },
    "konton": {
        "modell": Konto, "titel": "Kontoplan", "singular": "konto", "per_sida": 100,
        "kolumner": [("nummer", "Konto"), ("namn", "Namn"), ("momskod", "Momskod")],
        "sok": ["namn"], "sok_nummer": True,
        "falt": [("Konto", ["nummer", "namn", "aktiv", "momskod", "kostnadsstalle_tillatet", "projekt_tillatet"])],
        "hjalp": "Inaktiva konton döljs i listor men kan aktiveras när som helst. "
                 "Konton som används i bokföringen aktiveras automatiskt.",
    },
    "kostnadsstallen": {
        "modell": Kostnadsstalle, "titel": "Kostnadsställen", "singular": "kostnadsställe",
        "kolumner": [("nummer", "Nr"), ("namn", "Namn"), ("kortnamn", "Kortnamn")],
        "sok": ["nummer", "namn"],
        "falt": [("Kostnadsställe", ["nummer", "namn", "kortnamn", "aktiv"])],
    },
    "matchningsregler": {
        "modell": Matchningsregel, "titel": "Matchningsregler", "singular": "matchningsregel", "utan_aktiv": True,
        "kolumner": [("namn", "Namn"), ("text_innehaller", "Texten innehåller"), ("get_riktning_display", "Riktning"),
                     ("motkonto", "Konto"), ("automatisk", "Automatisk"), ("traffar", "Träffar")],
        "sok": ["namn", "text_innehaller"],
        "falt": [("Regel", ["namn", "text_innehaller", "riktning", "belopp", "motkonto", "momssats",
                            "verifikationstext", "automatisk", "aktiv"])],
        "hjalp": "Regler används vid bankavstämning. En regel som bokför automatiskt används så fort en "
                 "bankpost passar; övriga visas som förslag på bankposten.",
    },
    "inventarier": {
        "modell": Inventarie, "titel": "Inventarier", "singular": "inventarie", "utan_aktiv": True,
        "kolumner": [("namn", "Namn"), ("anskaffningsdatum", "Anskaffad"), ("anskaffningsvarde", "Värde"),
                     ("livslangd_ar", "År"), ("avyttrad", "Såld/utrangerad")],
        "sok": ["namn", "anteckning"],
        "falt": [("Inventarie", ["namn", "anskaffningsdatum", "anskaffningsvarde", "livslangd_ar", "avyttrad",
                                 "anteckning"]),
                 ("Bokföring", ["tillgangskonto", "ack_konto", "avskrivningskonto"])],
        "hjalp": "Inventarier som ska skrivas av enligt plan. Avskrivningen bokförs i bokslutet "
                 "(Bokföring → Bokslut). Inventarier under ett halvt prisbasbelopp kostnadsförs direkt på 5410.",
    },
    "betalningsvillkor": {
        "modell": Betalningsvillkor, "titel": "Betalningsvillkor", "singular": "betalningsvillkor",
        "kolumner": [("kod", "Kod"), ("dagar", "Dagar"), ("beskrivning", "Beskrivning")],
        "sok": ["kod", "beskrivning"], "utan_aktiv": True,
        "falt": [("Villkor", ["kod", "dagar", "beskrivning"])],
    },
}


def _cfg(typ):
    if typ not in REGISTER:
        raise Http404
    return REGISTER[typ]


def _formklass(cfg):
    falt = [f for _, grupp in cfg["falt"] for f in grupp]
    widgets = {"anteckning": forms.Textarea(attrs={"rows": 3})}
    form = forms.modelform_factory(cfg["modell"], fields=falt, widgets=widgets)
    for namn in ("standardkonto", "intaktskonto", "motkonto"):
        if namn in form.base_fields:
            form.base_fields[namn].queryset = Konto.objects.filter(aktiv=True)
    for falt in form.base_fields.values():
        if isinstance(falt, forms.DateField):
            falt.widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    return form


def lista(request, typ):
    cfg = _cfg(typ)
    qs = cfg["modell"].objects.all()
    visa = request.GET.get("visa", "aktiva")
    if not cfg.get("utan_aktiv") and visa == "aktiva":
        qs = qs.filter(aktiv=True)
    q = request.GET.get("q", "").strip()
    if q:
        filt = Q()
        for f in cfg["sok"]:
            filt |= Q(**{f"{f}__icontains": q})
        if q.isdigit() and "nummer" in [k for k, _ in cfg["kolumner"]]:
            filt |= Q(nummer__startswith=q) if cfg.get("sok_nummer") else Q(nummer=q)
        qs = qs.filter(filt)
    sida = Paginator(qs, cfg.get("per_sida", 50)).get_page(request.GET.get("sida"))
    rader = []
    for o in sida:
        celler = []
        for f, _ in cfg["kolumner"]:
            v = getattr(o, f, "")
            v = v() if callable(v) else ("" if v is None else v)
            celler.append(("Ja" if v else "Nej") if isinstance(v, bool) else v)
        rader.append({"obj": o, "celler": celler,
                      "url": cfg["detalj"](o) if "detalj" in cfg else reverse("register_andra", args=[typ, o.pk])})
    return render(request, "register/lista.html", {
        "cfg": cfg, "typ": typ, "sida": sida, "rader": rader, "q": q, "visa": visa})


def form(request, typ, pk=None):
    cfg = _cfg(typ)
    obj = get_object_or_404(cfg["modell"], pk=pk) if pk else None
    Form = _formklass(cfg)
    initial = {}
    if not obj and "nummer" in Form.base_fields and cfg["modell"] in (Kund, Leverantor):
        initial["nummer"] = (cfg["modell"].objects.aggregate(m=Max("nummer"))["m"] or 0) + 1
    f = Form(request.POST or None, instance=obj, initial=initial)
    if obj and typ == "konton":
        f.fields.pop("nummer")  # kontonumret är nyckeln och ändras inte
    if request.method == "POST":
        if "radera" in request.POST and obj:
            try:
                obj.delete()
                messages.success(request, f"{cfg['singular'].capitalize()} raderad.")
                return redirect("register_lista", typ=typ)
            except ProtectedError:
                messages.error(request, "Kan inte raderas eftersom den används. Avmarkera 'aktiv' i stället.")
                return redirect(request.path)
        if f.is_valid():
            o = f.save()
            messages.success(request, f"{cfg['singular'].capitalize()} {o} sparad.")
            return nasta_eller(request, cfg["detalj"](o) if "detalj" in cfg else reverse("register_lista", args=[typ]))
    grupper = [(rubrik, [f[n] for n in falt if n in f.fields]) for rubrik, falt in cfg["falt"]]
    extra = {}
    if typ == "leverantorer" and obj:
        extra["inkop"] = obj.fakturor.order_by("-fakturadatum")[:15]
    return render(request, "register/form.html", {
        "cfg": cfg, "typ": typ, "obj": obj, "form": f, "grupper": grupper, **extra})


def foretag(request):
    obj = Foretag.hamta()
    Form = forms.modelform_factory(Foretag, exclude=[])
    f = Form(request.POST or None, instance=obj)
    for n in ("kundfordringskonto",):
        f.fields[n].queryset = Konto.objects.filter(aktiv=True)
    f.fields["last_tom"].widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    if request.method == "POST" and f.is_valid():
        f.save()
        messages.success(request, "Företagsuppgifterna sparades.")
        return redirect("foretag")
    grupper = [
        ("Företaget", ["namn", "orgnr", "momsnummer", "f_skatt"]),
        ("Adress och kontakt", ["adress", "postnummer", "ort", "telefon", "epost", "webb"]),
        ("Betalning till dig", ["bankgiro", "plusgiro", "iban", "bic", "drojsmalsranta"]),
        ("Fakturering", ["var_referens", "nasta_fakturanummer", "standardartikel"]),
        ("Bokföring", ["momsperiod", "kundfordringskonto", "last_tom"]),
        ("Skatt och ägare", ["fskatt_manad", "agarandel", "sparat_utdelningsutrymme"]),
    ]
    return render(request, "register/form.html", {
        "cfg": {"titel": "Företagsuppgifter", "singular": "företagsuppgifter"}, "typ": None, "obj": obj,
        "form": f, "grupper": [(r, [f[n] for n in falt]) for r, falt in grupper], "ingen_radera": True})
