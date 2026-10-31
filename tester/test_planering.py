"""Planering: frister (Skatteverkets datum) och likviditetsprognos."""

import datetime as dt

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from bokforing import tjanster as bok
from bokforing.models import Bankpost, Leverantorsfaktura
from planering import frister, likviditet
from register.models import Foretag

from .grund import anvandare, d, datum, faktura, grunddata


class Frister(SimpleTestCase):
    def test_arbetsgivardeklaration_2026_enligt_skatteverkets_tabell(self):
        # Skatteverket: lön utbetald jan–dec 2026 deklareras dessa dagar
        vantat = ["2026-02-12", "2026-03-12", "2026-04-13", "2026-05-12", "2026-06-12", "2026-07-13",
                  "2026-08-17", "2026-09-14", "2026-10-12", "2026-11-12", "2026-12-14", "2027-01-18"]
        self.assertEqual([str(frister.agi_dag(dt.date(2026, m, 1))) for m in range(1, 13)], vantat)

    def test_kvartalsmoms(self):
        self.assertEqual(str(frister.momsdag(datum("2026-03-31"), "kvartal")), "2026-05-12")
        self.assertEqual(str(frister.momsdag(datum("2026-06-30"), "kvartal")), "2026-08-17")
        self.assertEqual(str(frister.momsdag(datum("2026-09-30"), "kvartal")), "2026-11-12")
        self.assertEqual(str(frister.momsdag(datum("2026-12-31"), "kvartal")), "2027-02-12")

    def test_manadsmoms_och_helar(self):
        self.assertEqual(str(frister.momsdag(datum("2026-11-30"), "manad")), "2027-01-18")
        self.assertEqual(str(frister.momsdag(datum("2026-12-31"), "ar")), "2027-02-26")

    def test_vardag(self):
        self.assertEqual(str(frister.vardag(datum("2026-08-01"))), "2026-08-03")  # lördag
        self.assertEqual(str(frister.vardag(datum("2026-12-24"))), "2026-12-28")  # julafton–annandag, helg

    def test_momsperiod_for(self):
        self.assertEqual(frister.momsperiod_for(datum("2026-05-17"), "kvartal"),
                         (datum("2026-04-01"), datum("2026-06-30")))


class FristLista(TestCase):
    def setUp(self):
        grunddata()

    def test_lista_och_kalenderfil(self):
        lista = frister.frister(datum("2026-07-01"), datum("2026-08-31"), Foretag.hamta())
        rubriker = [(str(f["datum"]), f["rubrik"]) for f in lista]
        self.assertIn(("2026-07-13", "Skattedeklaration"), rubriker)
        self.assertIn(("2026-07-31", "Årsredovisning 2025"), rubriker)
        self.assertIn(("2026-08-03", "Inkomstdeklaration 2 (2025)"), rubriker)
        self.assertIn(("2026-08-17", "Moms 2026-04–06"), rubriker)
        ics = frister.ics(lista, Foretag.hamta()).decode()
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertIn("DTSTART;VALUE=DATE:20260803", ics)
        self.assertEqual(ics.count("BEGIN:VEVENT"), len(lista))
        self.assertEqual(frister.ics(lista, Foretag.hamta()), frister.ics(lista, Foretag.hamta()), "stabila UID")

    def test_sidor(self):
        self.client.force_login(anvandare())
        self.assertEqual(self.client.get(reverse("frister")).status_code, 200)
        r = self.client.get(reverse("frister_ics"))
        self.assertEqual(r["Content-Type"], "text/calendar; charset=utf-8")


class Likviditet(TestCase):
    def setUp(self):
        self.kund, self.lev = grunddata()
        Bankpost.objects.create(datum=datum("2026-04-30"), text="x", belopp=d("100"), saldo=d("50000"),
                                konto_id=1930, nyckel="a")
        Bankpost.objects.create(datum=datum("2026-05-01"), text="y", belopp=d("-1000"), konto_id=1930, nyckel="b")
        f = faktura(self.kund, fakturadatum="2026-04-20")  # 12 500 kr, förfaller 2026-05-20
        f.slutfor()
        bok.bokfor_kundfaktura(f)
        Leverantorsfaktura.objects.create(leverantor=self.lev, belopp=d("2500"), konto_id=5410,
                                          fakturadatum=datum("2026-04-25"), forfallodatum=datum("2026-05-25"))

    def test_saldo_och_poster(self):
        p = likviditet.prognos(datum("2026-05-04"), veckor=8)
        self.assertEqual(p["start"], d("49000"))
        self.assertEqual(p["kalla"], "enligt banken 2026-04-30")
        per_kat = {}
        for v in p["veckor"]:
            for k, b in v["kategorier"].items():
                per_kat[k] = per_kat.get(k, 0) + b
        self.assertEqual(per_kat["Kundfakturor"], d("12500"))
        self.assertEqual(per_kat["Leverantörsfakturor"], d("-2500"))
        # momsen för april (kvartal 2) betalas 17 augusti – utanför 8 veckor; inget för kvartal 1
        self.assertNotIn("Moms", per_kat)
        self.assertEqual(p["veckor"][-1]["saldo"], d("49000") + d("12500") - d("2500"))

    def test_momsen_kommer_pa_momsdagen(self):
        p = likviditet.prognos(datum("2026-05-04"), veckor=16)
        moms = [x for v in p["veckor"] for x in v["poster"] if x["kategori"] == "Moms"]
        self.assertEqual([(str(x["datum"]), x["belopp"]) for x in moms], [("2026-08-17", d("-2500"))])

    def test_sidan_och_oversikten(self):
        self.client.force_login(anvandare())
        self.assertEqual(self.client.get(reverse("likviditet") + "?veckor=26").status_code, 200)
        self.assertEqual(self.client.get(reverse("oversikt")).status_code, 200)
