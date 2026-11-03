"""Lön eller utdelning: inkomstskatt 2026, gränsbelopp och optimering."""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from planering import lon_utdelning as lu
from register.models import Foretag

from .grund import anvandare, grunddata


class Inkomstskatt2026(TestCase):
    def test_grundavdrag(self):
        pbb = D(59200)
        self.assertEqual(lu.grundavdrag(D(540000), pbb), D(17400))   # 0,293 pbb, avrundat uppåt
        self.assertEqual(lu.grundavdrag(D(100000), pbb), D(33400))   # 25 041,60 + 0,2 × 41 392
        self.assertEqual(lu.grundavdrag(D(170000), pbb), D(45600))   # 0,77 pbb
        self.assertEqual(lu.grundavdrag(D(20000), pbb), D(20000))    # högst inkomsten

    def test_jobbskatteavdrag(self):
        jsa = lu.jobbskatteavdrag(D(540000), D(17400), D("0.3238"), D(59200))
        self.assertEqual(jsa.quantize(D(1)), D(52390))
        # Skatteverkets översikt: högsta avdrag 53 147 kr vid genomsnittlig skattesats (KI cirka 32,84 %)
        self.assertAlmostEqual(float(lu.jobbskatteavdrag(D(600000), D(17345.6), D("0.3284"), D(59200))), 53147, delta=20)

    def test_skatt_pa_lon(self):
        s = lu.inkomstskatt(D(540000), D(0), D("32.38"), D("0.27"))
        self.assertEqual(s["statlig"], 0)
        self.assertEqual(s["skatt"], D(116739))
        h = lu.inkomstskatt(D(780000), D(0), D("32.38"), D("0.27"))
        self.assertEqual(h["statlig"], D("23920.00"))  # 20 % × (780 000 − 17 400 − 643 000)
        # tjänstebeskattad utdelning ger varken jobbskatteavdrag eller pensionsavgift
        u = lu.inkomstskatt(D(540000), D(100000), D("32.38"), D("0.27"))
        self.assertEqual(u["jobbskatteavdrag"], s["jobbskatteavdrag"])
        self.assertEqual(u["pensionsavgift"], s["pensionsavgift"])


class RekommenderadLon(TestCase):
    def test_2026(self):
        r = lu.rekommenderad_lon(2026, D("31.42"))
        self.assertEqual((r["sgi"]["lon"], r["sgi"]["kostnad"]), (D(592000), D(778006)))     # 10 pbb × 1,3142
        self.assertEqual((r["pension"]["lon"], r["pension"]["lon_manad"]), (D(673038), D(56087)))  # 8,07 ibb
        self.assertEqual(r["pension"]["kostnad"], D(884507))
        self.assertEqual(r["rekommenderad"], r["pension"])
        self.assertTrue(r["over_brytpunkt"])
        aldre = lu.rekommenderad_lon(2026, D("10.21"))
        self.assertEqual(aldre["pension"]["kostnad"], D(673038) + D(68717))


class Optimering(TestCase):
    def f(self, **kw):
        return lu.Forutsattningar(ar=2026, resultat_fore_agarlon=D(1500000), **kw)

    def test_basta_ar_basta(self):
        bast, alla = lu.optimera(self.f())
        self.assertTrue(all(bast["netto"] >= a["netto"] for a in alla))
        self.assertLessEqual(bast["utdelning"] - bast["utdelning_inom"], 2000)  # utdelning ≈ gränsbelopp
        self.assertEqual(bast["kvar_i_bolaget"], 0)
        g = bast["gransbelopp"]
        self.assertEqual(g["grundbelopp"], D(333600))  # 4 × IBB 2026, utdelning 2027
        self.assertEqual(g["lonebaserat"], ((bast["lon"] - 8 * D(83400)) / 2).quantize(D(1), rounding="ROUND_DOWN"))

    def test_maxbelopp(self):
        bast, _ = lu.optimera(self.f(max_uttag=D(900000)))
        self.assertLessEqual(bast["uttag"], 900000)
        self.assertGreater(bast["kvar_i_bolaget"], 0)
        inkl, _ = lu.optimera(self.f(max_uttag=D(900000), max_inklusive_avgifter=True))
        self.assertLessEqual(inkl["uttag_inkl_avgifter"], 900000)
        self.assertLess(inkl["netto"], bast["netto"])

    def test_villkor(self):
        bast, _ = lu.optimera(self.f(bara_inom_gransbelopp=True, max_uttag=D(600000)))
        self.assertLessEqual(bast["utdelning"], bast["gransbelopp"]["summa"])
        self.assertGreaterEqual(lu.optimera(self.f(min_lon=D(700000)))[0]["lon"], 700000)
        self.assertEqual(lu.optimera(self.f(min_lon=D(5000000)))[0], None)

    def test_fritt_eget_kapital_och_sparat(self):
        utan, _ = lu.optimera(self.f(max_uttag=D(800000)))
        med, _ = lu.optimera(self.f(max_uttag=D(800000), sparat=D(300000)))
        self.assertGreater(med["netto"], utan["netto"])


class Sida(TestCase):
    def setUp(self):
        grunddata()
        self.client.force_login(anvandare())

    def test_visa_och_spara(self):
        url = reverse("lon_och_utdelning")
        self.assertEqual(url, "/planering/lon-och-utdelning/")
        self.assertRedirects(self.client.get("/lon/lon-och-utdelning/?resultat=1"), url + "?resultat=1",
                             fetch_redirect_response=False)
        sida = self.client.get(url)
        self.assertContains(sida, "Rekommenderad lägsta lön")
        from verksamhet.templatetags.kr import kr
        self.assertContains(sida, kr(673038, 0))
        self.assertContains(sida, kr(884507, 0))
        self.assertContains(sida, "min_lon=673038")
        data = {"resultat": "1 200 000", "max_uttag": "800 000", "agarandel": "100", "kommunalskatt": "30,5",
                "begravningsavgift": "0,25", "arbetsgivaravgift": "31,42", "sparat": "0"}
        r = self.client.get(url, data)
        self.assertContains(r, "Bästa fördelningen")
        self.assertContains(r, "Jämför")
        r = self.client.post(url, {**data, "spara": "1", "max_inkl_avgifter": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("csrf", r["Location"])
        f = Foretag.hamta()
        self.assertEqual((f.kommunalskatt, f.max_uttag, f.max_uttag_inkl_avgifter), (D("30.50"), D(800000), True))
        self.assertContains(self.client.get(url), "800000")
        self.assertContains(self.client.get(url, {**data, "resultat": "x"}), "Förutsättningar")


class Claude(TestCase):
    def test_verktyg(self):
        from assistent.arbete import lon_eller_utdelning
        grunddata()
        r = lon_eller_utdelning("1 500 000", "900000")
        self.assertLessEqual(D(r["bast"]["lon"]) + D(r["bast"]["utdelning"]), 900000)
        self.assertTrue(r["jamforelse"])
        self.assertEqual(r["rekommenderad_lagsta_lon"]["Full allmän pension"]["lon_per_ar"], "673038")
