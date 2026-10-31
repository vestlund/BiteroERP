from django import forms

from register.models import Konto

from .models import Anstalld, Lonekorning, Lonerad, Lonespec


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kw):
        super().__init__(format="%Y-%m-%d", **kw)


class AnstalldForm(forms.ModelForm):
    class Meta:
        model = Anstalld
        exclude = []
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
