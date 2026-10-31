from django import forms

from config.formular import BeloppField
from register.models import Artikel, Kund

from .models import Faktura, Fakturarad, Tidpost, Uppdrag


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class UppdragForm(forms.ModelForm):
    class Meta:
        model = Uppdrag
        fields = ["kund", "namn", "artikel", "timpris", "kundreferens", "ordernummer", "kostnadsstalle",
                  "startdatum", "slutdatum", "budget_timmar", "aktiv", "anteckning"]
        widgets = {"startdatum": DateInput(), "slutdatum": DateInput(), "anteckning": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kund"].queryset = Kund.objects.filter(aktiv=True)
        self.fields["artikel"].queryset = Artikel.objects.filter(aktiv=True)


class TidpostForm(forms.ModelForm):
    class Meta:
        model = Tidpost
        fields = ["uppdrag", "datum", "timmar", "beskrivning", "fakturerbar"]
        widgets = {"datum": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["uppdrag"].queryset = Uppdrag.objects.filter(aktiv=True).select_related("kund")
        self.fields["timmar"].widget.attrs.update({"step": "0.25", "min": "0"})


class FakturaHuvudForm(forms.ModelForm):
    class Meta:
        model = Faktura
        fields = ["kund", "fakturadatum", "forfallodatum", "er_referens", "var_referens", "ordernummer",
                  "period", "meddelande"]
        widgets = {"fakturadatum": DateInput(), "forfallodatum": DateInput(),
                   "meddelande": forms.Textarea(attrs={"rows": 2})}
        help_texts = {"forfallodatum": "Lämna tomt så räknas det fram från kundens betalningsvillkor"}


FakturaradFormSet = forms.inlineformset_factory(
    Faktura, Fakturarad,
    fields=["artikel", "beskrivning", "antal", "enhet", "a_pris", "momssats", "konto"],
    extra=1, can_delete=True,
)


class FaktureraTidForm(forms.Form):
    kund = forms.ModelChoiceField(Kund.objects.filter(aktiv=True))
    till_och_med = forms.DateField(label="Tid till och med", widget=DateInput())
    fakturadatum = forms.DateField(widget=DateInput())
    en_rad_per_post = forms.BooleanField(
        label="En fakturarad per tidpost (annars en rad per uppdrag)", required=False)


class BetaldForm(forms.Form):
    betaldatum = forms.DateField(widget=DateInput())


class ExportForm(forms.Form):
    fran = forms.DateField(label="Från fakturadatum", widget=DateInput())
    till = forms.DateField(label="Till fakturadatum", widget=DateInput())
    med_betalningar = forms.BooleanField(label="Ta med betalningar (1930 mot kundfordran)", required=False)


class TidcellForm(forms.Form):
    """En ruta i veckorutnätet: timmar för ett uppdrag en dag."""

    uppdrag = forms.ModelChoiceField(Uppdrag.objects.all())
    datum = forms.DateField()
    timmar = BeloppField(min_value=0, max_value=24, max_digits=5, tomt_som_noll=True,
                     error_messages={"min_value": "Timmar måste vara 0–24", "max_value": "Timmar måste vara 0–24"})
