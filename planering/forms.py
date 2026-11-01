from django import forms

from config.formular import BeloppField
from register.models import Foretag


class SkatteuppgifterForm(forms.ModelForm):
    """F-skatt, ägarandel och sparat utdelningsutrymme på sidan Skatt och utdelning."""

    fskatt_manad = BeloppField(label="Debiterad F-skatt per månad", decimal_places=0, required=False, min_value=0)
    agarandel = BeloppField(label="Ägarandel %", min_value=0.01, max_value=100)
    sparat_utdelningsutrymme = BeloppField(label="Sparat utdelningsutrymme (senaste K10)", decimal_places=0,
                                           tomt_som_noll=True, required=False, min_value=0)

    class Meta:
        model = Foretag
        fields = ["fskatt_manad", "agarandel", "sparat_utdelningsutrymme"]


class EgenLonForm(forms.Form):
    egen_lon = BeloppField(label="Din egen lön förra året", decimal_places=0, required=False, min_value=0,
                           widget=forms.TextInput(attrs={"inputmode": "decimal", "style": "width:9rem"}))
