"""Skatteprognos och gränsbelopp enligt 3:12-reglerna från 2026."""


from django.test import TestCase
from django.urls import reverse

from bokforing import tjanster as bok
from bokforing.models import IngaendeBalans
from planering import skatt
from register.models import Foretag, Konto

from .grund import anvandare, d, datum, faktura, grunddata


class Gransbelopp(TestCase):
    def test_grundbelopp_2026_och_2027(self):
        self.assertEqual(skatt.gransbelopp(2026, loneunderlag=d("0"))["grundbelopp"], d("322400"))
        self.assertEqual(skatt.gransbelopp(2027, loneunderlag=d("0"))["grundbelopp"], d("333600"))

    def test_lonebaserat_utrymme(self):
        # (1 000 000 - 8 × 80 600) × 50 % = 177 600
        g = skatt.gransbelopp(2026, loneunderlag=d("1000000"))
        self.assertEqual(g["lonebaserat"], d("177600"))
        self.assertEqual(g["summa"], d("500000"))
        # löner under 8 ibb ger inget
        self.assertEqual(skatt.gransbelopp(2026, loneunderlag=d("600000"))["lonebaserat"], d("0"))
        # tak 50 × egen lön
        self.assertEqual(skatt.gransbelopp(2026, loneunderlag=d("1000000"), egen_lon=d("2000"))["lonebaserat"],
                         d("100000"))

    def test_agarandel_och_sparat(self):
        g = skatt.gransbelopp(2026, agarandel=d("50"), loneunderlag=d("2000000"), sparat=d("10000"))
        self.assertEqual(g["grundbelopp"], d("161200"))
        self.assertEqual(g["lonebaserat"], d("177600"))   # (1 000 000 - 644 800) / 2
        self.assertEqual(g["summa"], d("348800"))
        self.assertEqual(g["skatt"], d("69760"))


class Prognos(TestCase):
    def setUp(self):
        kund, _ = grunddata()
        Konto.objects.update_or_create(nummer=2091, defaults={"namn": "Balanserad vinst", "aktiv": True})
        f = faktura(kund, timmar="100", pris="1000", fakturadatum="2026-03-01")
        f.slutfor()
        bok.bokfor_kundfaktura(f)
        bok.bokfor(datum("2026-03-02"), "Lön", [(7210, d("50000")), (1930, d("-50000"))])
        fo = Foretag.hamta()
        fo.fskatt_manad = d("500")
        fo.save()

    def test_skatteprognos(self):
        p = skatt.skatteprognos(datum("2026-05-10"))
        self.assertEqual(p["hittills"], d("50000"))
        self.assertEqual(p["skatt"], d("10300"))
        self.assertEqual(p["fskatt"], d("6000"))
        self.assertEqual(p["skillnad"], d("4300"))

    def test_fritt_eget_kapital_fran_ingaende_balanser(self):
        # IB 2027 från Visma utan bokfört resultat: 1930 300 000, 2091 -100 000 (skillnaden 200 000 är resultat)
        IngaendeBalans.objects.create(ar=2027, konto_id=1930, belopp=d("300000"))
        IngaendeBalans.objects.create(ar=2027, konto_id=2091, belopp=d("-100000"))
        self.assertEqual(skatt.fritt_eget_kapital(2026), d("300000"))

    def test_fritt_eget_kapital_fran_bokforingen(self):
        self.assertEqual(skatt.fritt_eget_kapital(2026), d("50000"))

    def test_sidan(self):
        self.client.force_login(anvandare())
        self.assertContains(self.client.get(reverse("skatt") + "?egen_lon=600000"), "Gränsbelopp")
        self.client.post(reverse("skatt"), {"fskatt_manad": "12 000", "agarandel": "100",
                                            "sparat_utdelningsutrymme": "5 000"})
        fo = Foretag.hamta()
        self.assertEqual((fo.fskatt_manad, fo.sparat_utdelningsutrymme), (d("12000"), d("5000")))
        r = self.client.post(reverse("skatt"), {"fskatt_manad": "x", "agarandel": "100"}, follow=True)
        self.assertContains(r, "Kontrollera")
        r = self.client.post(reverse("skatt"), {"fskatt_manad": "1", "agarandel": "150"})
        self.assertContains(r, "Kontrollera")
        self.assertEqual(Foretag.hamta().agarandel, d("100"))
        self.assertContains(self.client.get(reverse("skatt") + "?egen_lon=abc"), "Gränsbelopp")
