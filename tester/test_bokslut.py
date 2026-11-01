"""Bokslut: periodiseringar, avskrivningar, skatt, årets resultat och INK2-underlag."""

from django.test import TestCase
from django.urls import reverse

from bokforing import bokslut
from bokforing import tjanster as bok
from bokforing.models import Inventarie, Verifikation
from register.models import Konto
from verksamhet.models import Tidpost, Uppdrag

from .grund import anvandare, d, datum, faktura, grunddata


class Grund(TestCase):
    def setUp(self):
        self.kund, self.lev = grunddata()
        for nr, namn in [(1220, "Inventarier"), (1229, "Ack avskrivningar"), (1790, "Förutbetalda/upplupna"),
                         (2099, "Årets resultat"), (2512, "Beräknad inkomstskatt"), (2990, "Upplupna kostnader"),
                         (6072, "Representation ej avdragsgill"), (7832, "Avskrivningar inventarier"),
                         (8910, "Skatt på årets resultat"), (8999, "Årets resultat")]:
            Konto.objects.update_or_create(nummer=nr, defaults={"namn": namn, "aktiv": True})
        f = faktura(self.kund, timmar="100", pris="1000", fakturadatum="2026-06-30")  # 100 000 kr intäkt
        f.slutfor()
        bok.bokfor_kundfaktura(f)
        bok.bokfor(datum("2026-07-01"), "Kostnad", [(5410, d("20000")), (1930, d("-20000"))])
        bok.bokfor(datum("2026-07-02"), "Representation", [(6072, d("1000")), (1930, d("-1000"))])


class Periodiseringar(Grund):
    def test_upplupen_intakt_med_aterforing(self):
        ver, ater = bokslut.bokfor_periodisering(2026, "upplupen_intakt", 3041, d("5000"), "december")
        self.assertEqual(str(ver.datum), "2026-12-31")
        self.assertEqual({t.konto_id: t.belopp for t in ver.transaktioner.all()}, {1790: d("5000"), 3041: d("-5000")})
        self.assertEqual(str(ater.datum), "2027-01-01")
        self.assertEqual({t.konto_id: t.belopp for t in ater.transaktioner.all()}, {1790: d("-5000"), 3041: d("5000")})

    def test_upplupen_kostnad(self):
        ver, _ = bokslut.bokfor_periodisering(2026, "upplupen_kostnad", 5410, d("300"), "")
        self.assertEqual({t.konto_id: t.belopp for t in ver.transaktioner.all()}, {5410: d("300"), 2990: d("-300")})

    def test_upparbetat_ej_fakturerat(self):
        u = Uppdrag.objects.create(kund=self.kund, namn="R", timpris=d("1000"))
        Tidpost.objects.create(uppdrag=u, datum=datum("2026-12-20"), timmar=d("10"))   # ofakturerad
        Tidpost.objects.create(uppdrag=u, datum=datum("2027-01-05"), timmar=d("10"))   # nästa år
        sen = faktura(self.kund, timmar="5", pris="1200", fakturadatum="2027-01-03")  # fakturerad efter årsskiftet
        Tidpost.objects.create(uppdrag=u, datum=datum("2026-12-28"), timmar=d("5"), fakturarad=sen.rader.first())
        self.assertEqual(bokslut.upparbetat_ej_fakturerat(2026), d("16000"))


class Avskrivningar(Grund):
    def test_plan_och_bokforing(self):
        inv = Inventarie.objects.create(namn="Dator", anskaffningsdatum=datum("2026-07-15"),
                                        anskaffningsvarde=d("36000"), livslangd_ar=3)
        self.assertEqual(inv.avskrivning(2026), d("6000"))   # 6 månader à 1 000
        self.assertEqual(inv.avskrivning(2027), d("12000"))
        self.assertEqual(inv.avskrivning(2029), d("6000"))   # sista halvåret
        self.assertEqual(inv.avskrivning(2030), d("0"))
        self.assertEqual(inv.bokfort_varde(2026), d("30000"))
        ver = bokslut.bokfor_avskrivningar(2026)
        self.assertEqual({t.konto_id: t.belopp for t in ver.transaktioner.all()}, {7832: d("6000"), 1229: d("-6000")})
        with self.assertRaisesMessage(bok.BokforingsFel, "redan"):
            bokslut.bokfor_avskrivningar(2026)

    def test_avyttrad(self):
        inv = Inventarie.objects.create(namn="Telefon", anskaffningsdatum=datum("2026-01-10"),
                                        anskaffningsvarde=d("12000"), livslangd_ar=1, avyttrad=datum("2026-04-01"))
        self.assertEqual(inv.avskrivning(2026), d("3000"))


class SkattOchResultat(Grund):
    def test_skatteberakning(self):
        b = bokslut.skatteberakning(2026)
        self.assertEqual(b["fore_skatt"], d("79000"))      # 100 000 - 20 000 - 1 000
        self.assertEqual(b["summa_ej_avdragsgilla"], d("1000"))
        self.assertEqual(b["underlag"], d("80000"))
        self.assertEqual(b["skatt"], d("16480"))           # 20,6 %

    def test_bokfor_skatt_och_resultat(self):
        bokslut.bokfor_skatt(2026, bokslut.skatteberakning(2026)["skatt"])
        ver = bokslut.bokfor_arets_resultat(2026)
        self.assertEqual({t.konto_id: t.belopp for t in ver.transaktioner.all()},
                         {8999: d("62520"), 2099: d("-62520")})   # 79 000 - 16 480
        with self.assertRaises(bok.BokforingsFel):
            bokslut.bokfor_skatt(2026, d("1"))
        with self.assertRaises(bok.BokforingsFel):
            bokslut.bokfor_arets_resultat(2026)
        # efter bokslut: resultaträkningen nollad, balansräkningen går ihop
        i = bokslut.ink2r(2026)
        self.assertEqual(i["tillgangar"], i["ek_skulder"])
        self.assertEqual(Verifikation.objects.filter(text__startswith="Bokslut 2026:").count(), 2)

    def test_ink2r(self):
        rader = {r["kod"]: r["belopp"] for r in bokslut.ink2r(2026)["rader"]}
        self.assertEqual(rader["7410"], 100000)
        self.assertEqual(rader["7513"], 21000)
        self.assertEqual(rader["7251"], 125000)
        self.assertEqual(rader["7369"], 25000)    # utgående moms
        self.assertEqual(rader["7450"], 79000)
        s = dict((p, b) for p, _, b in bokslut.ink2s(2026))
        self.assertEqual((s["4.1"], s["4.3c"], s["4.15"]), (79000, 1000, 80000))


class Sidan(Grund):
    def test_sidan_och_atgarder(self):
        self.client.force_login(anvandare())
        url = reverse("bokslut") + "?ar=2026"
        r = self.client.get(url)
        self.assertContains(r, "Checklista")
        self.assertContains(self.client.get(reverse("bokslut") + "?ar=x"), "Ogiltigt räkenskapsår")
        self.client.post(reverse("bokslut"), {"ar": "2026", "atgard": "periodisering", "typ": "upplupen_intakt",
                                              "konto": "3041", "belopp": "4 350,00", "text": "dec"})
        self.assertTrue(Verifikation.objects.filter(text__startswith="Bokslut 2026: Upplupen intäkt").exists())
        r = self.client.post(reverse("bokslut"), {"ar": "2026", "atgard": "periodisering", "typ": "upplupen_intakt",
                                                  "konto": "1930", "belopp": "1"})
        self.assertContains(r, "resultatkonto")
        self.client.post(reverse("bokslut"), {"ar": "2026", "atgard": "skatt"})
        self.client.post(reverse("bokslut"), {"ar": "2026", "atgard": "resultat"})
        self.assertEqual(Verifikation.objects.filter(text__startswith="Bokslut 2026:").count(), 3)
        self.assertEqual(self.client.get(reverse("register_lista", args=["inventarier"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("register_ny", args=["inventarier"])).status_code, 200)
