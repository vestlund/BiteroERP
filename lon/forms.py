from decimal import Decimal

from django import forms

from config.formular import BeloppField, DateInput
from register.models import Konto

from .models import Anstalld, Lonekorning, Lonerad, Lonespec


class AnstalldForm(forms.ModelForm):
    class Meta:
        model = Anstalld
        fields = ["nummer", "fornamn", "efternamn", "personnummer", "adress", "postnummer", "ort", "epost",
                  "telefon", "clearingnummer", "bankkonto", "befattning", "anstalld_fran", "anstalld_till",
                  "lonetyp", "manadslon", "timlon", "loneskonto", "skattemetod", "skattetabell", "skattekolumn",
                  "skatteprocent", "semesterdagar", "kostnadsstalle", "aktiv", "anteckning"]
        widgets = {"anstalld_fran": DateInput(), "anstalld_till": DateInput(),
                   "anteckning": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["loneskonto"].queryset = Konto.objects.filter(nummer__in=[7010, 7210, 7220])


ANSTALLD_GRUPPER = [
    ("Person", ["nummer", "fornamn", "efternamn", "personnummer", "aktiv", "adress", "postnummer", "ort",
                "epost", "telefon"]),
    ("Anställning", ["befattning", "anstalld_fran", "anstalld_till", "lonetyp", "manadslon", "timlon",
                     "loneskonto", "kostnadsstalle", "semesterdagar"]),
    ("Skatt", ["skattemetod", "skattetabell", "skattekolumn", "skatteprocent"]),
    ("Utbetalning", ["clearingnummer", "bankkonto"]),
    ("Övrigt", ["anteckning"]),
]


class LonekorningForm(forms.ModelForm):
    class Meta:
        model = Lonekorning
        fields = ["period", "utbetalningsdatum", "anteckning"]
        widgets = {"utbetalningsdatum": DateInput(), "period": forms.TextInput(attrs={"type": "month"})}

    anstallda = forms.ModelMultipleChoiceField(
        Anstalld.objects.filter(aktiv=True), widget=forms.CheckboxSelectMultiple, label="Anställda", required=True)


class KommaForm(forms.ModelForm):
    def __init__(self, data=None, *a, **kw):
        if data is not None:
            data = data.copy()
            for k in list(data.keys()):
                if k.endswith(("antal", "a_pris", "skatteavdrag")):
                    data[k] = data[k].replace(" ", "").replace(" ", "").replace(",", ".")
        super().__init__(data, *a, **kw)


class LonespecForm(KommaForm):
    class Meta:
        model = Lonespec
        fields = ["skatteavdrag", "meddelande"]
        widgets = {"skatteavdrag": forms.TextInput(attrs={"inputmode": "decimal"})}


class LoneradForm(KommaForm):
    class Meta:
        model = Lonerad
        fields = ["typ", "beskrivning", "antal", "a_pris"]
        widgets = {"antal": forms.TextInput(attrs={"inputmode": "decimal"}),
                   "a_pris": forms.TextInput(attrs={"inputmode": "decimal"})}


LoneradFormSet = forms.inlineformset_factory(Lonespec, Lonerad, form=LoneradForm, extra=2, can_delete=True)


class SkattetabellForm(forms.Form):
    fil = forms.FileField(label="Skattetabeller (CSV från Skatteverkets öppna data)")


class LonUtdelningForm(forms.Form):
    """Förutsättningar för att räkna på lön och utdelning (skickas med GET, inget sparas)."""

    resultat = BeloppField(label="Resultat före din lön", decimal_places=0,
                           help_text="Prognos för årets resultat före lön och arbetsgivaravgifter")
    max_uttag = BeloppField(label="Max uttag (lön + utdelning)", decimal_places=0, required=False, min_value=0,
                            help_text="Tomt = ingen gräns")
    max_inkl_avgifter = forms.BooleanField(label="Maxbeloppet inkluderar arbetsgivaravgifter", required=False)
    min_lon = BeloppField(label="Lägsta lön per år", decimal_places=0, required=False, min_value=0,
                          help_text="T.ex. för pension och sjukpenning")
    fritt_eget_kapital = BeloppField(label="Fritt eget kapital från tidigare år att dela ut", decimal_places=0,
                                     required=False, min_value=0)
    ovriga_loner = BeloppField(label="Övriga anställdas löner per år", decimal_places=0, required=False, min_value=0)
    sparat = BeloppField(label="Sparat utdelningsutrymme", decimal_places=0, required=False, min_value=0)
    agarandel = BeloppField(label="Ägarandel %", min_value=Decimal("0.01"), max_value=100)
    kommunalskatt = BeloppField(label="Kommunalskatt %", min_value=20, max_value=40)
    begravningsavgift = BeloppField(label="Begravnings- och kyrkoavgift %", required=False, min_value=0, max_value=3)
    arbetsgivaravgift = BeloppField(label="Arbetsgivaravgift %", min_value=0, max_value=40)
    bara_inom_gransbelopp = forms.BooleanField(label="Bara utdelning inom gränsbeloppet (20 %)", required=False)
