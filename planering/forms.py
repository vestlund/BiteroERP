from decimal import Decimal

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
    agarandel = BeloppField(label="Ägarandel %", min_value=Decimal("0.01"), max_value=100)
    kommunalskatt = BeloppField(label="Kommunalskatt %", min_value=20, max_value=40)
    begravningsavgift = BeloppField(label="Begravnings- och kyrkoavgift %", required=False, min_value=0, max_value=3)
    arbetsgivaravgift = BeloppField(label="Arbetsgivaravgift %", min_value=0, max_value=40)
