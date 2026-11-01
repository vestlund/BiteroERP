"""Claude-kopplingen: tid, fakturautkast, bankhändelser, manuella verifikationer, rättelser och rapporter."""

import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from assistent import arbete as a
from assistent.verktyg import VerktygsFel
from bokforing import tjanster as bok
from bokforing.models import Bankpost, Bilaga, Matchningsregel, Verifikation
from logg.models import Handelse
from register.models import Foretag, Konto
from verksamhet.models import Faktura, Tidpost, Uppdrag

from .grund import d, datum, faktura, grunddata


class Grund(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.inkorg = Path(self.tmp.name) / "inkorg"
        self.inkorg.mkdir()
        o = override_settings(INKORG_DIR=self.inkorg, MEDIA_ROOT=Path(self.tmp.name) / "media")
        o.enable()
        self.addCleanup(o.disable)
        self.kund, self.lev = grunddata()
        self.uppdrag = Uppdrag.objects.create(kund=self.kund, namn="Rådgivning", timpris=d("1000"))
        IB = [(1930, "10000"), (2081, "-10000")]
        from bokforing.models import IngaendeBalans
        for k, b in IB:
            Konto.objects.get_or_create(nummer=k, defaults={"namn": f"Konto {k}", "aktiv": True})
            IngaendeBalans.objects.create(ar=2026, konto_id=k, belopp=d(b))


class TidOchFaktura(Grund):
    def test_rapportera_tid(self):
        from assistent.server import rapportera_tid
        res = rapportera_tid(self.uppdrag.pk, "2026-03-02", "7,5", "Workshop")
        self.assertIn("7.50 h 2026-03-02 på Rådgivning", res["sparad"])
        self.assertTrue(Handelse.objects.filter(anvandare="Claude", kalla="MCP rapportera_tid").exists())
        self.assertIn("7.50 h", rapportera_tid(self.uppdrag.pk, "2026-03-02", "1")["obs"] or "")
        with self.assertRaisesMessage(VerktygsFel, "över 24"):
            a.rapportera_tid(self.uppdrag.pk, "2026-03-02", "16")
        with self.assertRaisesMessage(VerktygsFel, "uppdrag"):
            a.rapportera_tid(9999, "2026-03-02", "1")
        Uppdrag.objects.filter(pk=self.uppdrag.pk).update(aktiv=False)
        with self.assertRaises(VerktygsFel):
            a.rapportera_tid(self.uppdrag.pk, "2026-03-03", "1")

    def test_fakturautkast_av_tid(self):
        for dag, h in [("2026-03-02", "8"), ("2026-03-03", "4"), ("2026-04-01", "8")]:
            Tidpost.objects.create(uppdrag=self.uppdrag, datum=datum(dag), timmar=d(h))
        oft = a.ofakturerad_tid("2026-03-31")
        self.assertEqual((oft[0]["kund"], oft[0]["netto_exkl_moms"]), ("Kund AB", "12000.00"))
        self.assertEqual(a.uppdrag()[0]["ofakturerade_timmar"], "20.00")
        res = a.skapa_fakturautkast(self.kund.nummer, "2026-03-31", "2026-04-01")
        self.assertEqual((res["netto"], res["att_betala"]), ("12000.00", "15000"))
        f = Faktura.objects.get()
        self.assertEqual((f.status, f.nummer), (Faktura.UTKAST, None))
        self.assertFalse(Verifikation.objects.exists())
        self.assertEqual(Tidpost.objects.filter(fakturarad__isnull=True).count(), 1)
        with self.assertRaisesMessage(VerktygsFel, "ingen ofakturerad tid"):
            a.skapa_fakturautkast(self.kund.nummer, "2026-03-31")
        self.assertEqual(a.tid("2026-03-01", "2026-04-30")["summa_timmar"], "20.00")


class Bank(Grund):
    def post(self, belopp, text="X", nyckel="a"):
        return Bankpost.objects.create(datum=datum("2026-05-02"), text=text, belopp=d(belopp), konto_id=1930,
                                       nyckel=nyckel)

    def test_kundfaktura_fran_forslag(self):
        f = faktura(self.kund)
        f.slutfor()
        bok.bokfor_kundfaktura(f)
        p = self.post("12500", "INBETALNING KUND AB")
        lista = a.bankhandelser()
        forslag = lista[0]["forslag"][0]
        self.assertEqual((forslag["atgard"], forslag["id"]), ("kundfaktura", f.pk))
        res = a.hantera_bankhandelse(p.pk, "kundfaktura", f.pk)
        self.assertTrue(res["verifikation"].startswith("I"))
        f.refresh_from_db()
        self.assertEqual(f.status, Faktura.BETALD)
        self.assertEqual(a.bankhandelser(), [])
        with self.assertRaisesMessage(bok.BokforingsFel, "redan hanterad"):
            a.hantera_bankhandelse(p.pk, "konto", motkonto=6570)

    def test_mot_konto_med_regel_och_ignorera(self):
        p = self.post("-65", "AVGIFT")
        res = a.hantera_bankhandelse(p.pk, "konto", motkonto=6570, spara_regel=True, regeltext="AVGIFT")
        self.assertIn("6570 Bankkostnader: debet 65.00", res["kontering"])
        self.assertTrue(Matchningsregel.objects.filter(text_innehaller="AVGIFT", automatisk=True).exists())
        with self.assertRaisesMessage(VerktygsFel, "samma som bankkontot"):
            a.hantera_bankhandelse(self.post("-1", nyckel="b").pk, "konto", motkonto=1930)
        with self.assertRaisesMessage(VerktygsFel, "atgard"):
            a.hantera_bankhandelse(self.post("-2", nyckel="c").pk, "aterstall")
        q = self.post("5", "SWISH PRIVAT", nyckel="d")
        self.assertEqual(a.hantera_bankhandelse(q.pk, "ignorera")["status"], "ignorerad")


class Verifikationer(Grund):
    def test_ny_verifikation_med_underlag(self):
        (self.inkorg / "skattekonto.pdf").write_bytes(b"%PDF-1.4 skattekonto")
        rader = [{"konto": 1630, "debet": "5 000"}, {"konto": 1930, "kredit": "5000,00", "text": "Överföring"}]
        res = a.ny_verifikation("2026-05-10", "Överföring till skattekontot", rader, "skattekonto.pdf")
        self.assertEqual(res["verifikation"], "A1")
        ver = Verifikation.objects.get(serie="A")
        self.assertEqual(Bilaga.objects.get().verifikation, ver)
        self.assertFalse((self.inkorg / "skattekonto.pdf").exists())
        with self.assertRaisesMessage(VerktygsFel, "redan A1"):
            a.ny_verifikation("2026-05-10", "Igen", rader)

    def test_ny_verifikation_validering(self):
        for rader, fel in [([{"konto": 1630, "debet": "10"}], "minst två"),
                           ([{"konto": 1630, "debet": "10"}, {"konto": 1930, "kredit": "9"}], "balanserar inte"),
                           ([{"konto": 1630, "debet": "10", "kredit": "10"}, {"konto": 1930, "kredit": "10"}], "antingen"),
                           ([{"konto": 9999, "debet": "10"}, {"konto": 1930, "kredit": "10"}], "finns inte")]:
            with self.subTest(fel=fel), self.assertRaisesMessage(VerktygsFel, fel):
                a.ny_verifikation("2026-05-10", "Test", rader)
        with self.assertRaisesMessage(VerktygsFel, "text"):
            a.ny_verifikation("2026-05-10", " ", [{"konto": 1630, "debet": "1"}, {"konto": 1930, "kredit": "1"}])
        f = Foretag.hamta()
        f.last_tom = datum("2026-06-30")
        f.save()
        with self.assertRaisesMessage(bok.BokforingsFel, "låst"):
            a.ny_verifikation("2026-05-10", "Låst", [{"konto": 1630, "debet": "1"}, {"konto": 1930, "kredit": "1"}])

    def test_rattelse(self):
        a.ny_verifikation("2026-05-10", "Fel konto", [{"konto": 5410, "debet": "100"}, {"konto": 1930, "kredit": "100"}])
        with self.assertRaisesMessage(VerktygsFel, "orsaken"):
            a.ratta_verifikation("A1", "")
        res = a.ratta_verifikation("a1", "Fel kostnadskonto", datum="2026-05-11")
        self.assertEqual((res["verifikation"], res["rattar"]), ("A2", "A1"))
        self.assertIn("5410 Förbrukningsinventarier: kredit 100.00", res["kontering"])
        self.assertTrue(a.visa_verifikation("A1")["rattad"])
        with self.assertRaisesMessage(bok.BokforingsFel, "redan rättad"):
            a.ratta_verifikation("A1", "igen")
        with self.assertRaisesMessage(VerktygsFel, "själv en rättelse"):
            a.ratta_verifikation("A2", "x")
        f = faktura(self.kund)
        f.slutfor()
        ver = bok.bokfor_kundfaktura(f)
        with self.assertRaisesMessage(VerktygsFel, "makulera"):
            a.ratta_verifikation(ver.vernr, "x")
        with self.assertRaisesMessage(VerktygsFel, "finns inte"):
            a.ratta_verifikation("Z99", "x")


class Rapporter(Grund):
    def test_huvudbok_resultat_och_datum(self):
        bok.bokfor(datum("2026-02-01"), "Mus", [(5410, d("400")), (1930, d("-400"))])
        bok.bokfor(datum("2026-03-01"), "Arvode", [(1930, d("1000")), (3041, d("-1000"))])
        hb = a.huvudbok(1930, "2026-01-01", "2026-12-31")
        self.assertEqual((hb["ingaende_saldo"], hb["utgaende_saldo"], len(hb["transaktioner"])), ("10000.00", "10600.00", 2))
        rb = a.resultat_och_balans("2026-01-01", "2026-12-31")
        self.assertEqual((rb["intakter"], rb["resultat_fore_skatt"]), ("1000.00", "600.00"))
        self.assertIn({"konto": 1930, "namn": "Företagskonto", "saldo": "10600.00"}, rb["balansrakning"])
        self.assertTrue(a.viktiga_datum(400))
        self.assertIn("veckor", a.likviditet(4))
