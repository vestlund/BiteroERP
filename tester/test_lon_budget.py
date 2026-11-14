"""Lön (arbetsgivaravgifter, lönekörning, AGI) och budget (helgdagar, arbetsdagar)."""

from django.test import SimpleTestCase, TestCase

from bokforing import tjanster as bok
from bokforing.models import Verifikation
from budget import tjanster as budget
from lon import tjanster as lon
from lon.models import Anstalld, Lonekorning, Lonerad, Lonespec, avgiftsgrupp, berakna_avgift

from .grund import d, datum, grunddata


class Arbetsgivaravgifter(SimpleTestCase):
    """Satser 2026 enligt Skatteverket. Ändras de ska både lon/models.py och detta test uppdateras."""

    def test_grupper_2026(self):
        maj = datum("2026-05-25")
        self.assertEqual(avgiftsgrupp(1980, maj), ("full", d("31.42")))
        self.assertEqual(avgiftsgrupp(1958, maj), ("aldre", d("10.21")), "fyllt 67 vid årets ingång")
        self.assertEqual(avgiftsgrupp(1959, maj), ("full", d("31.42")))
        self.assertEqual(avgiftsgrupp(1937, maj), ("ingen", d("0")))
        self.assertEqual(avgiftsgrupp(2005, maj), ("unga", d("20.81")))
        self.assertEqual(avgiftsgrupp(2003, maj), ("unga", d("20.81")))
        self.assertEqual(avgiftsgrupp(2002, maj), ("full", d("31.42")))
        self.assertEqual(avgiftsgrupp(2008, maj), ("full", d("31.42")))
        self.assertEqual(avgiftsgrupp(None, maj), ("full", d("31.42")))

    def test_ungdomsnedsattning_galler_bara_under_perioden(self):
        self.assertEqual(avgiftsgrupp(2005, datum("2026-03-31"))[0], "full")
        self.assertEqual(avgiftsgrupp(2005, datum("2026-04-01"))[0], "unga")
        self.assertEqual(avgiftsgrupp(2006, datum("2027-09-30"))[0], "unga")
        self.assertEqual(avgiftsgrupp(2006, datum("2027-10-01"))[0], "full")

    def test_tak_for_unga(self):
        avgift, grupp = berakna_avgift(d("30000"), 2005, datum("2026-05-25"))
        self.assertEqual(grupp, "unga")
        self.assertEqual(avgift, d("6773.50"))  # 25000*20.81% + 5000*31.42%
        self.assertEqual(berakna_avgift(d("40000"), 1980, datum("2026-05-25"))[0], d("12568.00"))


class Lonekorningar(TestCase):
    def setUp(self):
        grunddata()
        self.anst = Anstalld.objects.create(nummer=1, fornamn="Anna", efternamn="Test", personnummer="19800101-1234",
                                            manadslon=d("40000"), skattemetod="procent", skatteprocent=d("30"))
        self.korning = Lonekorning.objects.create(period="2026-05", utbetalningsdatum=datum("2026-05-25"))
        self.spec = Lonespec.objects.create(korning=self.korning, anstalld=self.anst)
        lon.standardrader(self.spec)
        Lonerad.objects.create(spec=self.spec, typ="formansbil", antal=1, a_pris=d("3000"))
        Lonerad.objects.create(spec=self.spec, typ="traktamente", antal=2, a_pris=d("290"))
        lon.uppdatera_skatt(self.spec)

    def test_berakning(self):
        b = self.spec.berakna()
        self.assertEqual((b["brutto"], b["formaner"], b["underlag"]), (d("40000"), d("3000"), d("43000")))
        self.assertEqual(b["skatt"], d("12900"))
        self.assertEqual(b["netto"], d("40000") - d("12900") + d("580"))
        self.assertEqual(b["avgift"], d("13510.60"))
        self.assertEqual(b["rutor"], {"011": d("40000"), "012": d("0"), "013": d("3000")})

    def test_bokforing_balanserar_och_laser(self):
        v = lon.bokfor_lonekorning(self.korning)
        rader = {t.konto_id: t.belopp for t in v.transaktioner.all()}
        self.assertEqual(sum(rader.values()), 0)
        self.assertEqual(rader[2710], d("-12900"))
        self.assertEqual(rader[2730], d("-13510.60"))
        self.assertEqual(rader[1930], d("-27680"))
        self.assertEqual(self.korning.status, Lonekorning.BOKFORD)
        with self.assertRaises(bok.BokforingsFel):
            lon.bokfor_lonekorning(self.korning)
        lon.las_upp(self.korning, datum("2026-05-26"))
        self.assertEqual(Verifikation.objects.filter(serie="S").count(), 2)

    def test_agi(self):
        lon.bokfor_lonekorning(self.korning)
        a = lon.agi("2026-05")
        i = a["individer"][self.anst]
        self.assertEqual((i["011"], i["013"], i["001"]), (d("40000"), d("3000"), d("12900")))
        self.assertEqual(a["summa_avgift"], d("13510.60"))
        self.assertEqual(lon.agi("2026-06")["summa_skatt"], 0)


class Arbetsdagar(SimpleTestCase):
    def test_pask(self):
        self.assertEqual(budget._pask(2026), datum("2026-04-05"))
        self.assertEqual(budget._pask(2027), datum("2027-03-28"))

    def test_helgdagar_2026(self):
        h = budget.helgdagar(2026)
        for dag in ("2026-01-01", "2026-01-06", "2026-04-03", "2026-04-06", "2026-05-01", "2026-05-14",
                    "2026-06-06", "2026-06-19", "2026-12-24", "2026-12-25", "2026-12-26", "2026-12-31"):
            self.assertIn(datum(dag), h)

    def test_arbetsdagar_2026(self):
        a = budget.arbetsdagar(2026)
        self.assertEqual(a[0], 20)    # januari: 22 vardagar minus nyårsdagen och trettondagen
        self.assertEqual(a[11], 20)   # december: 23 vardagar minus julafton, juldagen, nyårsafton
        self.assertEqual(len(a), 12)


class IntaktPaArbetsmanad(TestCase):
    """Kundfakturor räknas i budgetuppföljningen på den månad arbetet avser, inte på fakturadatum."""

    def setUp(self):
        from .grund import faktura
        self.kund, _ = grunddata()
        self.faktura = faktura

    def _bokford(self, fakturadatum, period=""):
        f = self.faktura(self.kund, fakturadatum=fakturadatum)
        f.period = period
        f.save()
        bok.bokfor_kundfaktura(f)
        return f

    def test_regler_for_manad(self):
        from verksamhet.models import Faktura
        f = Faktura(fakturadatum=datum("2026-11-02"))
        self.assertEqual(budget.intaktsmanad(f), (2026, 10))
        f.fakturadatum = datum("2026-11-15")
        self.assertEqual(budget.intaktsmanad(f), (2026, 10))
        f.fakturadatum = datum("2026-11-28")
        self.assertEqual(budget.intaktsmanad(f), (2026, 11))
        f.fakturadatum = datum("2027-01-05")
        self.assertEqual(budget.intaktsmanad(f), (2026, 12))
        f.period = "September 2026"
        self.assertEqual(budget.intaktsmanad(f), (2026, 9))
        f.period = "2026-08-01 – 2026-08-31"
        self.assertEqual(budget.intaktsmanad(f), (2026, 8))
        self.assertEqual(budget.intaktsmanad(f, {(2026, 6): d("10"), (2026, 7): d("30")}), (2026, 7))

    def test_utfall_per_arbetsmanad(self):
        from verksamhet.models import Tidpost, Uppdrag
        self._bokford("2026-11-02")                          # oktober
        self._bokford("2026-11-20")                          # november
        self._bokford("2026-11-03", period="Augusti 2026")  # augusti enligt perioden
        self._bokford("2027-01-04")                          # december 2026
        self._bokford("2026-01-05")                          # december 2025, inte med 2026
        f = self._bokford("2026-04-02")
        u = Uppdrag.objects.create(kund=self.kund, namn="Uppdrag", timpris=d("1000"))
        Tidpost.objects.create(uppdrag=u, datum=datum("2026-02-27"), timmar=d("8"), fakturarad=f.rader.first())
        intakter = budget.utfall(2026)["intakter"]
        self.assertEqual(intakter[9], d("10000.00"))
        self.assertEqual(intakter[10], d("10000.00"))
        self.assertEqual(intakter[7], d("10000.00"))
        self.assertEqual(intakter[11], d("10000.00"))
        self.assertEqual(intakter[1], d("10000.00"))  # fakturerad tid i februari
        self.assertEqual(intakter[0] + intakter[2] + intakter[3], 0)
        self.assertEqual(sum(intakter), d("50000.00"))
        self.assertEqual(budget.utfall(2025)["intakter"][11], d("10000.00"))
