"""Formulären: att indata valideras, svenska tal accepteras och omdirigeringar stannar i systemet."""

from django.test import TestCase
from django.urls import reverse

from bokforing.models import Bankpost, Leverantorsfaktura, Matchningsregel, Verifikation
from budget.models import Budget, Budgetrad
from verksamhet.models import Tidpost, Uppdrag

from .grund import anvandare, d, datum, grunddata


class Grund(TestCase):
    def setUp(self):
        self.kund, self.lev = grunddata()
        self.client.force_login(anvandare())

    def meddelanden(self, svar):
        return [str(m) for m in svar.wsgi_request._messages]


class Bankposter(Grund):
    def setUp(self):
        super().setUp()
        self.post = Bankpost.objects.create(datum=datum("2026-04-02"), text="AVGIFT", belopp=d("-25"),
                                            konto_id=1930, nyckel="a")
        self.url = reverse("bankpost_hantera", args=[self.post.pk])

    def test_bokfor_mot_konto_och_spara_regel(self):
        self.client.post(self.url, {"atgard": "konto", "motkonto": "6570", "text": "Bankavgift",
                                    "spara_regel": "1", "regeltext": "AVGIFT"})
        self.post.refresh_from_db()
        self.assertEqual(self.post.verifikation.text, "Bankavgift")
        self.assertEqual(Matchningsregel.objects.get().motkonto_id, 6570)

    def test_okant_konto_ger_fel_och_ingen_bokforing(self):
        r = self.client.post(self.url, {"atgard": "konto", "motkonto": "9999"})
        self.assertIn("finns inte", " ".join(self.meddelanden(r)))
        self.assertFalse(Verifikation.objects.exists())

    def test_okand_atgard_och_saknat_id(self):
        self.client.post(self.url, {"atgard": "radera_allt"})
        self.client.post(self.url, {"atgard": "ver"})
        self.post.refresh_from_db()
        self.assertFalse(self.post.hanterad)

    def test_nasta_far_inte_leda_ut_ur_systemet(self):
        r = self.client.post(self.url, {"atgard": "ignorera", "nasta": "https://elak.example/"})
        self.assertEqual(r.url, reverse("bank"))
        r = self.client.post(self.url, {"atgard": "aterstall", "nasta": "//elak.example/"})
        self.assertEqual(r.url, reverse("bank"))
        r = self.client.post(self.url, {"atgard": "ignorera", "nasta": "/bank/?visa=alla"})
        self.assertEqual(r.url, "/bank/?visa=alla")


class Momsavrakning(Grund):
    def test_ogiltigt_datum(self):
        r = self.client.post(reverse("moms_avrakna"), {"fran": "igår", "till": "2026-03-31"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Verifikation.objects.exists())

    def test_omvand_period(self):
        r = self.client.post(reverse("moms_avrakna"), {"fran": "2026-03-31", "till": "2026-01-01"})
        self.assertIn("efter", " ".join(self.meddelanden(r)))


class Budgetar(Grund):
    def test_ny_budget_med_forslag_och_svenska_tal(self):
        r = self.client.post(reverse("budget_ny"), {"ar": "2027", "namn": "", "huvudbudget": "on",
                                                    "kalla": "forslag", "belaggning": "85,5", "timmar": "7,5"})
        b = Budget.objects.get()
        self.assertEqual((b.namn, b.ar, b.huvudbudget), ("Budget 2027", 2027, True))
        self.assertRedirects(r, reverse("budget_redigera", args=[b.pk]), fetch_redirect_response=False)

    def test_kopiera_budget_som_inte_finns(self):
        self.client.post(reverse("budget_ny"), {"ar": "2027", "kalla": "999"})
        self.assertFalse(Budget.objects.exists())

    def test_redigera_rader(self):
        u = Uppdrag.objects.create(kund=self.kund, namn="Rådgivning", timpris=d("1000"))
        b = Budget.objects.create(ar=2027, namn="B")
        data = {"namn": "Budget 2027", "huvudbudget": "on",
                "r-ny1-typ": "tid", "r-ny1-ordning": "1", "r-ny1-uppdrag": str(u.pk), "r-ny1-timpris": "1 250,50",
                "r-ny2-typ": "kostnad", "r-ny2-ordning": "2", "r-ny2-konto": "6570", "r-ny2-m1": "1 000"}
        data.update({f"r-ny1-m{m}": "160" for m in range(1, 13)})
        self.client.post(reverse("budget_redigera", args=[b.pk]), data)
        tid = Budgetrad.objects.get(typ="tid")
        self.assertEqual((tid.timpris, tid.beskrivning, tid.kund), (d("1250.50"), str(u), self.kund))
        self.assertEqual(tid.varden, [d("160")] * 12)
        kostnad = Budgetrad.objects.get(typ="kostnad")
        self.assertEqual((kostnad.beskrivning, kostnad.varden[0]), ("Bankkostnader", d("1000")))

    def test_befintlig_rad_uppdateras_och_raderas(self):
        b = Budget.objects.create(ar=2027, namn="B")
        rad = Budgetrad.objects.create(budget=b, typ="kostnad", beskrivning="Hyra", konto_id=6570)
        sida = self.client.get(reverse("budget_redigera", args=[b.pk])).content.decode()
        self.assertIn(f'name="r-{rad.pk}-m1"', sida)
        self.client.post(reverse("budget_redigera", args=[b.pk]), {
            "namn": "B", f"r-{rad.pk}-typ": "kostnad", f"r-{rad.pk}-beskrivning": "Hyra",
            f"r-{rad.pk}-konto": "6570", f"r-{rad.pk}-m1": "3 000"})
        self.assertEqual(Budgetrad.objects.get().varden[0], d("3000"))
        self.client.post(reverse("budget_redigera", args=[b.pk]), {
            "namn": "B", f"r-{rad.pk}-typ": "kostnad", f"r-{rad.pk}-radera": "1"})
        self.assertFalse(Budgetrad.objects.exists())

    def test_ogiltigt_tal_sparar_ingenting(self):
        b = Budget.objects.create(ar=2027, namn="B")
        r = self.client.post(reverse("budget_redigera", args=[b.pk]), {
            "namn": "Nytt namn", "r-ny1-typ": "kostnad", "r-ny1-konto": "6570", "r-ny1-m3": "tusen"})
        self.assertIn("sparades inte", " ".join(self.meddelanden(r)))
        b.refresh_from_db()
        self.assertEqual(b.namn, "B")
        self.assertFalse(Budgetrad.objects.exists())


class Tidrutan(Grund):
    def setUp(self):
        super().setUp()
        self.u = Uppdrag.objects.create(kund=self.kund, namn="Rådgivning", timpris=d("1000"))

    def cell(self, timmar, dag="2026-03-02"):
        return self.client.post(reverse("tid_cell"), {"uppdrag": self.u.pk, "datum": dag, "timmar": timmar})

    def test_satt_andra_och_ta_bort(self):
        self.assertEqual(self.cell("7,5").status_code, 204)
        self.assertEqual(Tidpost.objects.get().timmar, d("7.5"))
        self.cell("8")
        self.assertEqual(Tidpost.objects.get().timmar, d("8"))
        self.cell("")
        self.assertFalse(Tidpost.objects.exists())

    def test_text_till_fakturan_och_ej_fakturerbar(self):
        self.cell("8")
        r = self.client.post(reverse("tid_cell"), {"uppdrag": self.u.pk, "datum": "2026-03-02", "timmar": "8",
                                                   "beskrivning": " Workshop med kund ", "fakturerbar": "on",
                                                   "detaljer": "on"})
        self.assertEqual(r.status_code, 204)
        tp = Tidpost.objects.get()
        self.assertEqual((tp.beskrivning, tp.fakturerbar), ("Workshop med kund", True))
        self.client.post(reverse("tid_cell"), {"uppdrag": self.u.pk, "datum": "2026-03-02", "timmar": "8",
                                               "beskrivning": "Intern", "detaljer": "on"})
        self.assertFalse(Tidpost.objects.get().fakturerbar)
        self.cell("6")  # utan detaljer ändras bara timmarna
        tp = Tidpost.objects.get()
        self.assertEqual((tp.timmar, tp.beskrivning, tp.fakturerbar), (d("6"), "Intern", False))

    def test_flera_poster_slas_ihop_och_fakturerad_last(self):
        Tidpost.objects.create(uppdrag=self.u, datum=datum("2026-03-02"), timmar=d("2"), beskrivning="A")
        Tidpost.objects.create(uppdrag=self.u, datum=datum("2026-03-02"), timmar=d("3"), beskrivning="B")
        self.assertEqual(self.cell("5").status_code, 204)
        tp = Tidpost.objects.get()
        self.assertEqual((tp.timmar, tp.beskrivning), (d("5"), "A / B"))
        from .grund import faktura
        f = faktura(self.kund)
        Tidpost.objects.filter(pk=tp.pk).update(fakturarad=f.rader.first())
        self.assertEqual(self.cell("1").status_code, 400)
        self.assertEqual(Tidpost.objects.get().timmar, d("5"))

    def test_manadsvy(self):
        Tidpost.objects.create(uppdrag=self.u, datum=datum("2026-02-27"), timmar=d("7.5"), beskrivning="Workshop")
        r = self.client.get(reverse("tidrapport") + "?manad=2026-02")
        self.assertContains(r, "2026-02-01")
        self.assertContains(r, "2026-02-28")
        self.assertNotContains(r, "2026-03-01")
        self.assertContains(r, "Rådgivning")
        self.assertContains(r, 'value="Workshop"')
        self.assertNotContains(r, "Lägg till tid med beskrivning")
        # äldre länkar med ?vecka= visar den veckans månad, ogiltigt värde visar innevarande månad
        self.assertContains(self.client.get(reverse("tidrapport") + "?vecka=2026-02-23"), "2026-02-28")
        self.assertEqual(self.client.get(reverse("tidrapport") + "?manad=x").status_code, 200)

    def test_ogiltiga_varden(self):
        for timmar, dag in [("25", "2026-03-02"), ("-1", "2026-03-02"), ("åtta", "2026-03-02"), ("8", "x")]:
            with self.subTest(timmar=timmar, dag=dag):
                self.assertEqual(self.cell(timmar, dag).status_code, 400)
        self.assertFalse(Tidpost.objects.exists())


class Betalfil(Grund):
    def test_skapar_fil_for_valda_fakturor(self):
        lf = Leverantorsfaktura.objects.create(leverantor=self.lev, belopp=d("1250"), konto_id=5410)
        r = self.client.post(reverse("io_post", args=["betalfil"]), {"faktura": [lf.pk], "datum": "2026-04-30"})
        self.assertEqual(r["Content-Type"], "application/xml")
        self.assertIn(b"<ReqdExctnDt>2026-04-30", r.content)

    def test_ingen_faktura_vald(self):
        r = self.client.post(reverse("io_post", args=["betalfil"]), {"datum": "2026-04-30"})
        self.assertIn("minst en faktura", " ".join(self.meddelanden(r)))

    def test_betald_faktura_kan_inte_valjas(self):
        lf = Leverantorsfaktura.objects.create(leverantor=self.lev, belopp=d("1"), konto_id=5410,
                                               betaldatum=datum("2026-01-01"))
        r = self.client.post(reverse("io_post", args=["betalfil"]), {"faktura": [lf.pk], "datum": "2026-04-30"})
        self.assertEqual(r.status_code, 302)


class ValtAr(TestCase):
    def test_ogiltiga_ar_ger_standard(self):
        from config.formular import valt_ar
        self.assertEqual(valt_ar({"ar": "2025"}, 2026), (2025, True))
        self.assertEqual(valt_ar({}, 2026), (2026, True))
        self.assertEqual(valt_ar({"ar": "x"}, 2026), (2026, False))
        self.assertEqual(valt_ar({"ar": "99999"}, 2026), (2026, False))

    def test_sidor_med_ar_i_adressen(self):
        from django.urls import reverse

        from .grund import anvandare
        self.client.force_login(anvandare())
        for namn in ("moms", "budget_aktuell", "uppdragsuppfoljning", "bokslut"):
            with self.subTest(namn=namn):
                self.assertIn(self.client.get(reverse(namn) + "?ar=abc").status_code, (200,))
