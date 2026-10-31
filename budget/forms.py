from django import forms
from django.utils import timezone

from config.formular import BeloppField
from register.models import Konto
from verksamhet.models import Uppdrag

from .models import Budget, Budgetrad


class BudgetNyForm(forms.Form):
    ar = forms.IntegerField(min_value=2000, max_value=2100)
    namn = forms.CharField(max_length=100, required=False)
    huvudbudget = forms.BooleanField(required=False)
    kalla = forms.CharField(required=False, help_text="'forslag', id för budget att kopiera, eller tomt")
    belaggning = BeloppField(min_value=0, max_value=100, required=False)
    timmar = BeloppField(min_value=0, max_value=24, required=False)

    def clean_kalla(self):
        k = self.cleaned_data["kalla"]
        if k and k != "forslag":
            try:
                return Budget.objects.get(pk=int(k))
            except (ValueError, Budget.DoesNotExist):
                raise forms.ValidationError("Budgeten att kopiera finns inte.")
        return k

    def clean(self):
        d = super().clean()
        if d.get("ar") and not d.get("namn"):
            d["namn"] = f"Budget {d['ar']}"
        return d

    @staticmethod
    def standardar():
        idag = timezone.localdate()
        return idag.year if idag.month < 10 else idag.year + 1


class BudgetHuvudForm(forms.ModelForm):
    class Meta:
        model = Budget
        fields = ["namn", "huvudbudget"]


class BudgetradForm(forms.Form):
    """En rad i budgetrutnätet. Fälten heter r-<n>-<fält> i mallen, vilket är Djangos prefix-format."""

    typ = forms.ChoiceField(choices=Budgetrad.TYPER)
    ordning = forms.IntegerField(required=False)
    beskrivning = forms.CharField(max_length=200, required=False)
    uppdrag = forms.ModelChoiceField(Uppdrag.objects.all(), required=False)
    konto = forms.ModelChoiceField(Konto.objects.all(), required=False,
                                   error_messages={"invalid_choice": "Kontot finns inte i kontoplanen."})
    timpris = BeloppField(required=False, max_digits=10)
    radera = forms.BooleanField(required=False)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for m in range(1, 13):
            self.fields[f"m{m}"] = BeloppField(required=False, tomt_som_noll=True)

    @property
    def varden(self):
        return [self.cleaned_data[f"m{m}"] or 0 for m in range(1, 13)]
