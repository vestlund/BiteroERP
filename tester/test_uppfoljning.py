"""Uppdragsuppföljning: beläggning, intäkt per kund och varning för uppdrag som tar slut."""

from django.test import TestCase
from django.urls import reverse

from register.models import Kund
from verksamhet import uppfoljning
from verksamhet.models import Tidpost, Uppdrag

from .grund import anvandare, d, datum, faktura, grunddata


class Uppfoljning(TestCase):
    def setUp(self):
        self.kund, _ = grunddata()
        self.u = Uppdrag.objects.create(kund=self.kund, namn="Rådgivning", timpris=d("1000"),
                                        slutdatum=datum("2026-06-30"), budget_timmar=d("100"))
        Tidpost.objects.create(uppdrag=self.u, datum=datum("2026-01-15"), timmar=d("80"))
        Tidpost.objects.create(uppdrag=self.u, datum=datum("2026-02-10"), timmar=d("152"))
        Tidpost.objects.create(uppdrag=self.u, datum=datum("2026-02-11"), timmar=d("4"), fakturerbar=False)
        f = faktura(self.kund, timmar="80", pris="1000", fakturadatum="2026-01-31")
        f.slutfor()

    def test_belaggning(self):
        b = uppfoljning.belaggning(2026, datum("2026-02-20"))
        self.assertEqual([r["manad"] for r in b["manader"]], [1, 2])
        self.assertEqual(b["manader"][0]["mojliga"], d("160"))          # 20 arbetsdagar i januari
        self.assertEqual(b["manader"][0]["procent"], d("50"))
        self.assertEqual(b["manader"][1]["procent"], d("95"))           # 152 av 20 × 8
        self.assertEqual(b["timmar"], d("232"))

    def test_per_kund(self):
        lista, total = uppfoljning.per_kund(2026)
        self.assertEqual(total, d("80000"))
        r = lista[0]
        self.assertEqual((r["kund"], r["i_ar"], r["timmar"], r["andel"]), (self.kund, d("80000"), d("232"), d("100")))
        self.assertEqual(r["snittpris"], d("345"))

    def test_varning_nar_uppdrag_tar_slut(self):
        v = uppfoljning.varningar(datum("2026-05-15"))
        self.assertEqual(len(v), 1)
        self.assertIn("om 46 dagar", v[0]["varning"])
        self.assertEqual(uppfoljning.varningar(datum("2026-03-01")), [], "mer än 60 dagar kvar")
        # ett nytt uppdrag som tar vid tar bort varningen
        Uppdrag.objects.create(kund=Kund.objects.create(nummer=2, namn="Ny kund"), namn="Nästa",
                               startdatum=datum("2026-07-01"))
        self.assertEqual(uppfoljning.varningar(datum("2026-05-15")), [])

    def test_passerat_slutdatum(self):
        self.assertIn("passerat", uppfoljning.varningar(datum("2026-07-05"))[0]["varning"])

    def test_sidan_och_oversikten(self):
        self.client.force_login(anvandare())
        self.assertContains(self.client.get(reverse("uppdragsuppfoljning")), "Beläggning")
        self.assertEqual(self.client.get(reverse("oversikt")).status_code, 200)
