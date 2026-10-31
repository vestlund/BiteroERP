"""Gemensamma formulärdelar och säker omdirigering."""

from decimal import Decimal

from django import forms
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme


class BeloppField(forms.DecimalField):
    """Decimaltal skrivna på svenska: '1 234,50' (vanligt eller hårt mellanslag, komma som decimaltecken)."""

    def __init__(self, *a, tomt_som_noll=False, **kw):
        self.tomt_som_noll = tomt_som_noll
        kw.setdefault("max_digits", 14)
        kw.setdefault("decimal_places", 2)
        super().__init__(*a, **kw)

    def to_python(self, value):
        if isinstance(value, str):
            value = value.replace(" ", "").replace(" ", "").replace(",", ".")
            if value == "" and self.tomt_som_noll:
                return Decimal(0)
        return super().to_python(value)


def nasta_eller(request, standard, *args, **kwargs):
    """Omdirigerar till ?nasta/POST nasta om den pekar inom systemet, annars till standard."""
    nasta = request.POST.get("nasta") or request.GET.get("nasta")
    if nasta and url_has_allowed_host_and_scheme(nasta, allowed_hosts={request.get_host()},
                                                 require_https=request.is_secure()):
        return redirect(nasta)
    return redirect(standard, *args, **kwargs)
