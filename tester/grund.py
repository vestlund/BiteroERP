"""Gemensam testdata: en liten kontoplan, företag, kund och leverantör."""

import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model

from register.models import Betalningsvillkor, Foretag, Konto, Kund, Leverantor

KONTON = [
    (1510, "Kundfordringar", ""), (1630, "Skattekonto", ""), (1930, "Företagskonto", ""),
    (2440, "Leverantörsskulder", ""), (2611, "Utgående moms 25 %", "10"), (2621, "Utgående moms 12 %", "11"),
    (2641, "Ingående moms", "48"), (2650, "Redovisningskonto för moms", ""), (2710, "Personalskatt", ""),
    (2730, "Lagstadgade sociala avgifter", ""), (2893, "Skuld till närstående", ""),
    (3041, "Försäljning tjänster 25 %", "05-25%"), (3740, "Öres- och kronutjämning", ""),
    (5410, "Förbrukningsinventarier", ""), (6570, "Bankkostnader", ""), (7210, "Löner tjänstemän", ""),
    (7321, "Traktamenten", ""), (7385, "Bilförmån", ""), (7399, "Motkonto förmåner", ""), (7510, "Arbetsgivaravgifter", ""),
]


def grunddata():
    for nr, namn, momskod in KONTON:
        Konto.objects.update_or_create(nummer=nr, defaults={"namn": namn, "momskod": momskod, "aktiv": True})
    f = Foretag.hamta()
    f.namn, f.orgnr, f.iban, f.bic = "Testbolaget AB", "556000-0000", "SE4550000000058398257466", "ESSESESS"
    f.nasta_fakturanummer = 100
    f.save()
    villkor, _ = Betalningsvillkor.objects.get_or_create(kod="30", defaults={"dagar": 30})
    kund = Kund.objects.create(nummer=1, namn="Kund AB", betalningsvillkor=villkor)
    lev = Leverantor.objects.create(nummer=1, namn="Leverantör AB", bankgiro="5050-1055",
                                    standardkonto_id=5410)
    return kund, lev


def anvandare():
    return get_user_model().objects.create_superuser("test", "test@example.com", "hemligt-123")


def d(s):
    return Decimal(s)


def datum(s):
    return dt.date.fromisoformat(s)


def faktura(kund, timmar="10", pris="1000", fakturadatum="2026-03-10", moms="25"):
    from verksamhet.models import Faktura, Fakturarad
    f = Faktura.objects.create(kund=kund, fakturadatum=datum(fakturadatum))
    Fakturarad.objects.create(faktura=f, beskrivning="Konsulttjänster", antal=d(timmar), a_pris=d(pris),
                              momssats=d(moms), konto_id=3041)
    return f
