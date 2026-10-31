"""Röktest: varje sida i systemet ska gå att öppna utan fel, med data på plats."""

from django.test import TestCase
from django.urls import reverse

from bokforing import tjanster as bok
from bokforing.models import Bankpost, Leverantorsfaktura
from bokforing.io_views import POSTER
from budget.models import Budget
from lon import tjanster as lon
from lon.models import Anstalld, Lonekorning, Lonespec
from logg.models import Handelse, Systemversion
from register.views import REGISTER
from verksamhet.models import Tidpost, Uppdrag

from .grund import anvandare, d, datum, faktura, grunddata


class AllaSidor(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = anvandare()
        kund, lev = grunddata()
        cls.uppdrag = Uppdrag.objects.create(kund=kund, namn="Rådgivning", timpris=d("1000"))
        Tidpost.objects.create(uppdrag=cls.uppdrag, datum=datum("2026-03-02"), timmar=d("8"))
        cls.faktura = faktura(kund)
        cls.faktura.slutfor()
        cls.ver = bok.bokfor_kundfaktura(cls.faktura)
        cls.lf = Leverantorsfaktura.objects.create(leverantor=lev, belopp=d("1250"), moms=d("250"), konto_id=5410,
                                                   fakturadatum=datum("2026-03-01"))
        bok.bokfor_levfaktura(cls.lf)
        cls.post = Bankpost.objects.create(datum=datum("2026-03-03"), text="Avgift", belopp=d("-25"), konto_id=1930,
                                           nyckel="x")
        cls.anst = Anstalld.objects.create(nummer=1, fornamn="Anna", efternamn="Test", personnummer="19800101-1234",
                                           manadslon=d("40000"), skattemetod="procent", skatteprocent=d("30"))
        cls.korning = Lonekorning.objects.create(period="2026-05", utbetalningsdatum=datum("2026-05-25"))
        cls.spec = Lonespec.objects.create(korning=cls.korning, anstalld=cls.anst)
        lon.standardrader(cls.spec)
        cls.budget = Budget.objects.create(ar=2026, namn="Budget 2026")
        cls.sv = Systemversion.objects.create(tid=cls.ver.skapad, version="test", fingeravtryck="0" * 64, filer={})

    def setUp(self):
        self.client.force_login(self.user)

    def oppna(self, url, status=200):
        r = self.client.get(url)
        self.assertEqual(r.status_code, status, f"{url} gav {r.status_code}")
        return r

    def test_sidor_utan_parametrar(self):
        for namn in ["oversikt", "budget_aktuell", "budgetar", "budget_ny", "io_lista", "lonekorningar",
                     "lonekorning_ny", "anstallda", "anstalld_ny", "agi", "skattetabeller", "tidrapport",
                     "uppdrag_lista", "uppdrag_nytt", "kund_lista", "faktura_lista", "fakturera_tid", "faktura_ny",
                     "bokforing", "verifikationer", "verifikation_ny", "levfakturor", "levfaktura_ny", "bank",
                     "moms", "rapporter", "huvudbok", "sie", "foretag", "logg", "systemdokumentation"]:
            with self.subTest(namn):
                self.oppna(reverse(namn))
        self.oppna(reverse("sok") + "?q=Kund")
        self.oppna(reverse("levfaktura_ny") + "?typ=kvitto")

    def test_sidor_med_objekt(self):
        h = Handelse.objects.first()
        for namn, pk in [("faktura_detalj", self.faktura.pk), ("uppdrag_detalj", self.uppdrag.pk),
                         ("uppdrag_andra", self.uppdrag.pk), ("kund_detalj", self.faktura.kund_id),
                         ("verifikation", self.ver.pk), ("levfaktura", self.lf.pk),
                         ("lonekorning", self.korning.pk), ("lonespec", self.spec.pk), ("anstalld", self.anst.pk),
                         ("anstalld_andra", self.anst.pk), ("budget_uppfoljning", self.budget.pk),
                         ("budget_redigera", self.budget.pk), ("logg_handelse", h.pk),
                         ("logg_systemversion", self.sv.pk), ("lonespec_utskrift", self.korning.pk)]:
            with self.subTest(namn):
                self.oppna(reverse(namn, args=[pk]))
        self.oppna(reverse("faktura_utskrift", args=[self.faktura.pk]) + "?genererad=1")

    def test_register_och_import_export(self):
        for typ in REGISTER:
            with self.subTest(typ):
                self.oppna(reverse("register_lista", args=[typ]))
                self.oppna(reverse("register_ny", args=[typ]))
        for slug, _ in POSTER:
            with self.subTest(slug):
                self.client.get(reverse("io_post", args=[slug]))  # vissa svarar med fil direkt
                r = self.client.get(reverse("io_post", args=[slug]))
                self.assertIn(r.status_code, (200, 302))

    def test_exporter(self):
        for namn in ["fakturor_csv", "tid_export_csv", "logg_csv"]:
            with self.subTest(namn):
                self.oppna(reverse(namn))
        r = self.oppna(reverse("logg") + "?kontrollera=1")
        self.assertContains(r, "Kedjan är hel")

    def test_kraver_inloggning(self):
        self.client.logout()
        for url in ["/", reverse("verifikationer"), reverse("logg")]:
            self.assertEqual(self.client.get(url).status_code, 302)


class Driftlage(TestCase):
    def setUp(self):
        self.client.force_login(anvandare())

    def test_egen_404_sida_och_sakerhetshuvuden(self):
        r = self.client.get("/finns-inte/")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "Sidan finns inte", status_code=404)
        self.assertEqual(self.client.get("/").headers["X-Frame-Options"], "SAMEORIGIN")

    def test_debug_ar_av(self):
        from django.conf import settings
        self.assertFalse(settings.DEBUG)
        self.assertIn("whitenoise.middleware.WhiteNoiseMiddleware", settings.MIDDLEWARE)
