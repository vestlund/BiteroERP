from decimal import Decimal

from django import forms
from django.utils import timezone

from config.formular import BeloppField
from register.models import Konto, Leverantor

from .bokslut import PERIODISERINGAR
from .models import Leverantorsfaktura


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kw):
        super().__init__(format="%Y-%m-%d", **kw)


class KontoField(forms.ModelChoiceField):
    def __init__(self, qs=None, **kw):
        super().__init__(qs if qs is not None else Konto.objects.filter(aktiv=True), **kw)

    def label_from_instance(self, k):
        return f"{k.nummer} {k.namn}"


class VerRadForm(forms.Form):
    konto = forms.IntegerField(required=False, widget=forms.TextInput(
        attrs={"list": "kontolista", "class": "kontoinput", "placeholder": "Konto", "autocomplete": "off"}))
    debet = forms.DecimalField(required=False, max_digits=14, decimal_places=2,
                               widget=forms.TextInput(attrs={"inputmode": "decimal", "class": "debet"}))
    kredit = forms.DecimalField(required=False, max_digits=14, decimal_places=2,
                                widget=forms.TextInput(attrs={"inputmode": "decimal", "class": "kredit"}))
    text = forms.CharField(required=False, max_length=300)

    def clean(self):
        d = super().clean()
        if (d.get("debet") or d.get("kredit")) and not d.get("konto"):
            raise forms.ValidationError("Ange konto.")
        if d.get("konto") and not Konto.objects.filter(pk=d["konto"]).exists():
            raise forms.ValidationError(f"Konto {d['konto']} finns inte.")
        return d

    @property
    def belopp(self):
        d = self.cleaned_data
        return (d.get("debet") or Decimal(0)) - (d.get("kredit") or Decimal(0))


# DecimalField accepterar inte komma – byt till punkt innan validering
class KommaMixin:
    decimalfalt = ()

    def __init__(self, data=None, *a, **kw):
        if data is not None:
            data = data.copy()
            for k in list(data.keys()):
                if any(k.endswith(f) for f in self.decimalfalt):
                    data[k] = data[k].replace(" ", "").replace(" ", "").replace(",", ".")
        super().__init__(data, *a, **kw)


class VerRadKomma(KommaMixin, VerRadForm):
    decimalfalt = ("debet", "kredit")


VerRadFormSet = forms.formset_factory(VerRadKomma, extra=8, max_num=60)


class VerifikationForm(forms.Form):
    datum = forms.DateField(widget=DateInput(), initial=timezone.localdate)
    text = forms.CharField(max_length=300)
    bilaga = forms.FileField(required=False, help_text="Kvitto, avtal, kontoutdrag … (PDF eller bild)")


class LevfakturaForm(KommaMixin, forms.ModelForm):
    decimalfalt = ("belopp", "moms")
    konto = KontoField(Konto.objects.filter(aktiv=True, nummer__gte=1000, nummer__lt=9000), label="Kostnadskonto")
    betalkonto = KontoField(Konto.objects.filter(aktiv=True).filter(nummer__in=[1910, 1930, 1940, 2890, 2893]),
                            required=False, label="Betalt från")
    bilaga = forms.FileField(required=False, help_text="PDF eller foto på fakturan/kvittot")
    momssats = forms.ChoiceField(choices=[("25", "25 %"), ("12", "12 %"), ("6", "6 %"), ("0", "0 %"),
                                          ("", "Eget belopp")], required=False, label="Moms",
                                 initial="25")

    class Meta:
        model = Leverantorsfaktura
        fields = ["typ", "leverantor", "motpart", "fakturanummer", "ocr", "fakturadatum", "forfallodatum",
                  "beskrivning", "belopp", "momssats", "moms", "konto", "kostnadsstalle", "betalkonto", "bilaga"]
        widgets = {"fakturadatum": DateInput(), "forfallodatum": DateInput(),
                   "belopp": forms.TextInput(attrs={"inputmode": "decimal"}),
                   "moms": forms.TextInput(attrs={"inputmode": "decimal"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["leverantor"].queryset = Leverantor.objects.filter(aktiv=True).order_by("namn")
        self.fields["leverantor"].label_from_instance = lambda l: l.namn
        self.fields["leverantor"].required = False
        if not self.initial.get("betalkonto"):
            self.initial["betalkonto"] = 1930

    def clean(self):
        d = super().clean()
        if d.get("typ") == Leverantorsfaktura.FAKTURA and not d.get("leverantor"):
            self.add_error("leverantor", "Välj leverantör för en leverantörsfaktura (eller välj typ Kvitto).")
        return d


class BetalaForm(forms.Form):
    datum = forms.DateField(widget=DateInput(), initial=timezone.localdate)


class BankimportForm(forms.Form):
    fil = forms.FileField(label="Kontoutdrag (CSV)")
    konto = KontoField(Konto.objects.filter(nummer__gte=1900, nummer__lt=2000, aktiv=True),
                       initial=1930, label="Bankkonto i bokföringen")


class PeriodForm(forms.Form):
    fran = forms.DateField(label="Från", widget=DateInput())
    till = forms.DateField(label="Till", widget=DateInput())


class SieExportForm(PeriodForm):
    endast_nya = forms.BooleanField(label="Bara verifikationer som inte exporterats tidigare", required=False,
                                    initial=True)
    serier = forms.MultipleChoiceField(
        choices=[("F", "F Kundfakturor"), ("I", "I Inbetalningar"), ("L", "L Leverantörsfakturor/kvitton"),
                 ("U", "U Utbetalningar"), ("B", "B Bank"), ("M", "M Moms"), ("S", "S Lön"), ("A", "A Manuella")],
        widget=forms.CheckboxSelectMultiple, initial=["F", "I", "L", "U", "B", "M", "S", "A"], required=False)


class SieImportForm(forms.Form):
    fil = forms.FileField(label="SIE-fil från Visma (typ 4 eller 1)")


class BankpostAtgardForm(forms.Form):
    """Vad som ska göras med en bankpost (knapparna på bank-sidan)."""

    ATGARDER = [("kundfaktura", "Kundfaktura betald"), ("levfaktura", "Leverantörsfaktura betald"),
                ("ver", "Koppla till befintlig verifikation"), ("konto", "Bokför mot konto"),
                ("regel", "Bokför enligt regel"), ("ignorera", "Ignorera"), ("aterstall", "Koppla bort")]
    atgard = forms.ChoiceField(choices=ATGARDER)
    id = forms.IntegerField(required=False, min_value=1)
    motkonto = forms.ModelChoiceField(Konto.objects.all(), required=False,
                                      error_messages={"invalid_choice": "Kontot finns inte i kontoplanen."})
    text = forms.CharField(max_length=300, required=False)
    spara_regel = forms.BooleanField(required=False)
    regeltext = forms.CharField(max_length=100, required=False)

    def clean(self):
        d = super().clean()
        atgard = d.get("atgard")
        if atgard in ("kundfaktura", "levfaktura", "ver", "regel") and not d.get("id"):
            raise forms.ValidationError("Det saknas vilken faktura, verifikation eller regel som avses.")
        if atgard == "konto" and not d.get("motkonto"):
            self.add_error("motkonto", "Ange motkonto.")
        if d.get("spara_regel") and not d.get("regeltext", "").strip():
            self.add_error("regeltext", "Ange vilken text regeln ska känna igen.")
        return d


class MomsperiodForm(PeriodForm):
    def clean(self):
        d = super().clean()
        if d.get("fran") and d.get("till") and d["fran"] > d["till"]:
            raise forms.ValidationError("Från-datum är efter till-datum.")
        return d


class BetalfilForm(forms.Form):
    faktura = forms.ModelMultipleChoiceField(
        Leverantorsfaktura.objects.filter(typ=Leverantorsfaktura.FAKTURA, betaldatum__isnull=True),
        error_messages={"required": "Välj minst en faktura."})
    datum = forms.DateField(initial=timezone.localdate)


class SieInlasningForm(forms.Form):
    fil = forms.FileField()
    vad = forms.ChoiceField(choices=[("ver", "Verifikationer"), ("ib", "Ingående balanser")], initial="ver",
                            required=False)


def mappningsform(falt, rubriker):
    """Formulär för att para ihop fälten i en importfil med systemets fält (m_<fält> = kolumnindex)."""
    val = [("", "–")] + [(str(i), r) for i, r in enumerate(rubriker)]
    attrs = {k: forms.TypedChoiceField(choices=val, coerce=int, required=False, empty_value=None)
             for k, *_ in falt}
    return type("MappningForm", (forms.Form,), {f"m_{k}": f for k, f in attrs.items()})


class PeriodiseringForm(forms.Form):
    typ = forms.ChoiceField(choices=[(k, v[0]) for k, v in PERIODISERINGAR.items()])
    konto = forms.ModelChoiceField(
        Konto.objects.filter(nummer__gte=3000, nummer__lt=9000), label="Resultatkonto",
        error_messages={"invalid_choice": "Kontot finns inte (ange ett resultatkonto 3000–8999)."})
    belopp = BeloppField(min_value=Decimal("0.01"))
    text = forms.CharField(max_length=120, required=False)

