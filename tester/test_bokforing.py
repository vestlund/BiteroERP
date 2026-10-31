"""Bokföringens kärna: verifikationer, rättelser, låsning, kund- och leverantörsfakturor, moms och saldon."""


from django.test import TestCase

from bokforing import tjanster as bok
from bokforing.models import Leverantorsfaktura, OforanderligError, Transaktion, Verifikation
from register.models import Foretag

from .grund import d, datum, faktura, grunddata


class Verifikationer(TestCase):
    def setUp(self):
        grunddata()

    def test_bokfor_skapar_balanserad_verifikation(self):
        v = bok.bokfor(datum("2026-02-01"), "Bankavgift", [(6570, d("50")), (1930, d("-50"))], anvandare="test")
        self.assertEqual(v.vernr, "A1")
        self.assertEqual(sum(t.belopp for t in v.transaktioner.all()), 0)
        self.assertEqual(v.skapad_av, "test")

    def test_obalans_stoppas(self):
        with self.assertRaisesMessage(bok.BokforingsFel, "balanserar inte"):
            bok.bokfor(datum("2026-02-01"), "Fel", [(6570, d("50")), (1930, d("-49"))])
        self.assertFalse(Verifikation.objects.exists())

    def test_minst_tva_rader_och_text(self):
        with self.assertRaises(bok.BokforingsFel):
            bok.bokfor(datum("2026-02-01"), "En rad", [(6570, d("0")), (1930, d("0"))])
        with self.assertRaises(bok.BokforingsFel):
            bok.bokfor(datum("2026-02-01"), "  ", [(6570, d("1")), (1930, d("-1"))])

    def test_okant_konto_stoppas(self):
        with self.assertRaisesMessage(bok.BokforingsFel, "9999"):
            bok.bokfor(datum("2026-02-01"), "X", [(9999, d("1")), (1930, d("-1"))])

    def test_nummerserie_per_serie_och_ar_utan_luckor(self):
        a = [bok.bokfor(datum("2026-01-0%d" % i), "x", [(6570, 1), (1930, -1)]).nummer for i in range(1, 4)]
        b = bok.bokfor(datum("2026-01-05"), "x", [(6570, 1), (1930, -1)], serie="B").nummer
        c = bok.bokfor(datum("2027-01-05"), "x", [(6570, 1), (1930, -1)]).nummer
        self.assertEqual(a, [1, 2, 3])
        self.assertEqual((b, c), (1, 1))

    def test_last_period_stoppar_bokforing(self):
        f = Foretag.hamta()
        f.last_tom = datum("2026-03-31")
        f.save()
        with self.assertRaisesMessage(bok.BokforingsFel, "låst"):
            bok.bokfor(datum("2026-03-31"), "x", [(6570, 1), (1930, -1)])
        bok.bokfor(datum("2026-04-01"), "x", [(6570, 1), (1930, -1)])

    def test_verifikation_och_transaktion_gar_inte_att_andra_eller_radera(self):
        v = bok.bokfor(datum("2026-02-01"), "x", [(6570, 1), (1930, -1)])
        v.text = "ändrad"
        with self.assertRaises(OforanderligError):
            v.save()
        with self.assertRaises(OforanderligError):
            v.delete()
        t = v.transaktioner.first()
        t.belopp = d("2")
        with self.assertRaises(OforanderligError):
            t.save()
        with self.assertRaises(OforanderligError):
            t.delete()
        v.save(update_fields=["sie_exporterad"])  # enda tillåtna ändringen

    def test_rattelse_vander_alla_rader_och_bara_en_gang(self):
        v = bok.bokfor(datum("2026-02-01"), "Fel konto", [(5410, d("100")), (1930, d("-100"))])
        r = bok.rattelse(v, datum("2026-02-02"))
        self.assertEqual(r.rattar, v)
        self.assertEqual(sorted(t.belopp for t in r.transaktioner.all()), [d("-100"), d("100")])
        self.assertEqual(bok.saldon(datum("2026-01-01"), datum("2026-12-31"))[5410]["ub"], 0)
        with self.assertRaisesMessage(bok.BokforingsFel, "redan rättad"):
            bok.rattelse(v, datum("2026-02-03"))
        self.assertEqual(bok.aktiva_ver(Verifikation.objects.all()).count(), 0)


class Kundfakturor(TestCase):
    def setUp(self):
        self.kund, _ = grunddata()

    def test_summering_och_oresavrundning(self):
        f = faktura(self.kund, timmar="1", pris="99.99")
        s = f.summering()
        self.assertEqual(s["netto"], d("99.99"))
        self.assertEqual(s["moms_total"], d("25.00"))
        self.assertEqual(s["att_betala"], d("125"))
        self.assertEqual(s["oresavrundning"], d("0.01"))

    def test_slutfor_ger_nummer_och_forfallodatum(self):
        f = faktura(self.kund)
        f.slutfor()
        self.assertEqual(f.nummer, 100)
        self.assertEqual(str(f.forfallodatum), "2026-04-09")
        self.assertEqual(Foretag.hamta().nasta_fakturanummer, 101)
        g = faktura(self.kund)
        g.slutfor()
        self.assertEqual(g.nummer, 101)

    def test_bokforing_av_faktura_och_inbetalning(self):
        f = faktura(self.kund)
        f.slutfor()
        v = bok.bokfor_kundfaktura(f)
        rader = {t.konto_id: t.belopp for t in v.transaktioner.all()}
        self.assertEqual(rader, {1510: d("12500"), 3041: d("-10000"), 2611: d("-2500")})
        self.assertIsNone(bok.bokfor_kundfaktura(f), "samma faktura får inte bokföras två gånger")
        i = bok.bokfor_inbetalning(f, datum("2026-04-01"))
        self.assertEqual(i.serie, "I")
        s = bok.saldon(datum("2026-01-01"), datum("2026-12-31"))
        self.assertEqual(s[1510]["ub"], 0)
        self.assertEqual(s[1930]["ub"], d("12500"))

    def test_faktura_med_oresavrundning_balanserar(self):
        f = faktura(self.kund, timmar="1", pris="99.99")
        f.slutfor()
        v = bok.bokfor_kundfaktura(f)
        self.assertEqual(sum(t.belopp for t in v.transaktioner.all()), 0)
        self.assertEqual(v.transaktioner.get(konto_id=3740).belopp, d("-0.01"))


class Leverantorsfakturor(TestCase):
    def setUp(self):
        _, self.lev = grunddata()

    def test_faktura_och_betalning(self):
        lf = Leverantorsfaktura.objects.create(leverantor=self.lev, fakturanummer="A1", belopp=d("1250"),
                                               moms=d("250"), konto_id=5410, fakturadatum=datum("2026-03-01"))
        v = bok.bokfor_levfaktura(lf)
        self.assertEqual({t.konto_id: t.belopp for t in v.transaktioner.all()},
                         {5410: d("1000"), 2641: d("250"), 2440: d("-1250")})
        self.assertFalse(lf.betald)
        bok.bokfor_utbetalning(lf, datum("2026-03-30"))
        lf.refresh_from_db()
        self.assertEqual(str(lf.betaldatum), "2026-03-30")
        self.assertEqual(bok.saldon(datum("2026-01-01"), datum("2026-12-31"))[2440]["ub"], 0)

    def test_kvitto_bokfors_mot_betalkonto_och_blir_betalt(self):
        lf = Leverantorsfaktura.objects.create(typ="kvitto", motpart="Kafé", belopp=d("112"), moms=d("12"),
                                               konto_id=5410, betalkonto_id=2893,
                                               fakturadatum=datum("2026-03-05"))
        v = bok.bokfor_levfaktura(lf)
        self.assertEqual(v.transaktioner.get(konto_id=2893).belopp, d("-112"))
        lf.refresh_from_db()
        self.assertEqual(lf.betaldatum, lf.fakturadatum)


class Moms(TestCase):
    def setUp(self):
        kund, lev = grunddata()
        f = faktura(kund, fakturadatum="2026-02-10")
        f.slutfor()
        bok.bokfor_kundfaktura(f)
        lf = Leverantorsfaktura.objects.create(leverantor=lev, belopp=d("1250"), moms=d("250"), konto_id=5410,
                                               fakturadatum=datum("2026-02-15"))
        bok.bokfor_levfaktura(lf)

    def test_momsrapportens_rutor(self):
        r = bok.momsrapport(datum("2026-01-01"), datum("2026-03-31"))["rutor"]
        self.assertEqual(r["05"], d("10000"))
        self.assertEqual(r["10"], d("2500"))
        self.assertEqual(r["48"], d("250"))
        self.assertEqual(r["49"], d("2250"))

    def test_momsavrakning_nollar_momskonton_en_gang(self):
        v = bok.bokfor_momsavrakning(datum("2026-01-01"), datum("2026-03-31"), datum("2026-03-31"))
        self.assertEqual(v.transaktioner.get(konto_id=2650).belopp, d("-2250"))
        s = bok.saldon(datum("2026-01-01"), datum("2026-03-31"))
        self.assertEqual((s[2611]["ub"], s[2641]["ub"]), (0, 0))
        with self.assertRaisesMessage(bok.BokforingsFel, "redan avräknad"):
            bok.bokfor_momsavrakning(datum("2026-01-01"), datum("2026-03-31"), datum("2026-03-31"))
        # avräkningen ska inte påverka rapporten för perioden
        self.assertEqual(bok.momsrapport(datum("2026-01-01"), datum("2026-03-31"))["rutor"]["49"], d("2250"))

    def test_annan_period_ar_tom(self):
        r = bok.momsrapport(datum("2026-04-01"), datum("2026-06-30"))["rutor"]
        self.assertEqual(r.get("49"), 0)


class Saldon(TestCase):
    def setUp(self):
        grunddata()

    def test_ingaende_balans_och_period(self):
        from bokforing.models import IngaendeBalans
        IngaendeBalans.objects.create(ar=2026, konto_id=1930, belopp=d("1000"))
        IngaendeBalans.objects.create(ar=2026, konto_id=3041, belopp=d("-5"))  # resultatkonton nollställs
        bok.bokfor(datum("2026-01-15"), "jan", [(6570, 100), (1930, -100)])
        bok.bokfor(datum("2026-02-15"), "feb", [(6570, 50), (1930, -50)])
        s = bok.saldon(datum("2026-02-01"), datum("2026-02-28"))
        self.assertEqual(s[1930], {"ib": d("900"), "period": d("-50"), "ub": d("850")})
        self.assertEqual(s[6570]["ib"], d("100"))
        self.assertNotIn(3041, s)

    def test_decimalsummor_avrundas_till_oren(self):
        for _ in range(10):
            bok.bokfor(datum("2026-01-15"), "x", [(6570, d("0.10")), (1930, d("-0.10"))])
        self.assertEqual(bok.saldon(datum("2026-01-01"), datum("2026-12-31"))[6570]["ub"], d("1.00"))
        self.assertEqual(Transaktion.objects.count(), 20)
