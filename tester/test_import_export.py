"""SIE-export och -import, Visma-fakturor ur SIE, bankfiler, avstämning och betalfil."""

from django.test import TestCase

from bokforing import avstamning, bankimport, betalfil, fakturaimport, sie
from bokforing import tjanster as bok
from bokforing.models import Bankpost, Leverantorsfaktura, Matchningsregel, Verifikation
from register.models import Foretag, Kund
from verksamhet.models import Faktura

from .grund import d, datum, faktura, grunddata

VISMA_SIE = """#FLAGGA 0
#FORMAT PC8
#SIETYP 4
#FNAMN "Testbolaget AB"
#RAR 0 20260101 20261231
#KONTO 1510 "Kundfordringar"
#KONTO 1930 "Företagskonto"
#KONTO 2611 "Utgående moms 25%"
#KONTO 3041 "Försäljning tjänst"
#KONTO 2440 "Leverantörsskulder"
#KONTO 2641 "Ingående moms"
#KONTO 6540 "IT-tjänster"
#IB 0 1930 5000.00
#VER "F" "12" 20260105 "Kundfaktura till 1 Kund AB, 170"
{
   #TRANS 1510 {} 12500.00
   #TRANS 3041 {} -10000.00
   #TRANS 2611 {} -2500.00
}
#VER "F" "13" 20260110 "Kundfaktura till 1 Kund AB, 171"
{
   #TRANS 1510 {} 6250.00
   #TRANS 3041 {} -5000.00
   #TRANS 2611 {} -1250.00
}
#VER "I" "4" 20260204 "Inbetalning från 1 Kund AB, 170"
{
   #TRANS 1930 {} 12500.00
   #TRANS 1510 {} -12500.00
}
#VER "L" "7" 20260115 "Leverantörsfaktura från 3 Moln, Data & Drift AB, 4471"
{
   #TRANS 2440 {} -1250.00
   #TRANS 6540 {} 1000.00
   #TRANS 2641 {} 250.00
}
#VER "A" "9" 20260120 "Kundfaktura 100 Kund AB (F1)"
{
   #TRANS 1510 {} 100.00
   #TRANS 3041 {} -100.00
}
""".encode("cp437")


class SieExport(TestCase):
    def setUp(self):
        grunddata()

    def test_export_kan_lasas_tillbaka(self):
        bok.bokfor(datum("2026-03-01"), "Bankavgift mars", [(6570, d("49.50")), (1930, d("-49.50"))])
        data, antal = sie.exportera(Verifikation.objects.all(), Foretag.hamta())
        self.assertEqual(antal, 1)
        self.assertIn(b"#SIETYP 4", data)
        vers, konton = sie.las_verifikationer(data)
        self.assertEqual(len(vers), 1)
        self.assertEqual(vers[0]["datum"], datum("2026-03-01"))
        self.assertTrue(vers[0]["text"].endswith("(A1)"))
        self.assertEqual(sorted(b for _, b, _ in vers[0]["rader"]), [d("-49.50"), d("49.50")])
        self.assertEqual(konton[6570], "Bankkostnader")


class SieImport(TestCase):
    def setUp(self):
        grunddata()

    def test_ingaende_balans(self):
        ar, antal = sie.importera_ib(VISMA_SIE)
        self.assertEqual((ar, antal), (2026, 1))
        self.assertEqual(bok.saldon(datum("2026-01-01"), datum("2026-01-01"))[1930]["ib"], d("5000"))

    def test_verifikationer_importeras_en_gang_och_egna_hoppas_over(self):
        bok.bokfor(datum("2026-01-20"), "Kundfaktura 100 Kund AB", [(1510, 100), (3041, -100)], serie="F")
        res = sie.importera_verifikationer(VISMA_SIE)
        self.assertEqual((res["nya"], res["egna"], res["fanns"]), (4, 1, 0))
        self.assertTrue(Verifikation.objects.filter(serie="V", extern_id="F-12-20260105").exists())
        res2 = sie.importera_verifikationer(VISMA_SIE)
        self.assertEqual((res2["nya"], res2["fanns"]), (0, 4))
        self.assertTrue(Verifikation.objects.filter(transaktioner__konto_id=6540).exists(), "saknat konto skapas")


class VismaFakturorUrSie(TestCase):
    def setUp(self):
        grunddata()

    def test_visma_text(self):
        self.assertEqual(fakturaimport.visma_text("Kundfaktura till 4 Secure State Cyber AB, 123"),
                         ("4", "Secure State Cyber AB", "123"))
        self.assertEqual(fakturaimport.visma_text("Leverantörsfaktura från 3 Moln, Data & Drift AB, 4471"),
                         ("3", "Moln, Data & Drift AB", "4471"))
        self.assertIsNone(fakturaimport.visma_text("Bankavgift"))

    def test_kundfakturor_och_betalningar(self):
        poster, fel, betalningar = fakturaimport.fran_sie(VISMA_SIE, "kund")
        self.assertEqual(fel, [])
        self.assertEqual(betalningar, 1)
        self.assertEqual([(p["nummer"], p["belopp"], p["betald"]) for p in poster],
                         [(170, d("12500"), True), (171, d("6250"), False)])
        res = fakturaimport.importera_kundfakturor(poster)
        self.assertEqual(res["nya"], 2)
        self.assertEqual(res["nasta_nummer"], 172)
        f170, f171 = Faktura.objects.get(nummer=170), Faktura.objects.get(nummer=171)
        self.assertEqual((f170.status, f171.status), (Faktura.BETALD, Faktura.SKICKAD))
        self.assertEqual(f171.summering()["att_betala"], d("6250"))
        self.assertTrue(f171.fran_visma)
        self.assertFalse(f171.verifikationer.exists(), "Visma-fakturor bokförs inte här")
        self.assertEqual(Kund.objects.count(), 1, "kunden känns igen på kundnummer")
        # ny import uppdaterar i stället för att skapa dubbletter
        self.assertEqual(fakturaimport.importera_kundfakturor(poster)["uppdaterade"], 2)

    def test_leverantorsfakturor(self):
        poster, fel, _ = fakturaimport.fran_sie(VISMA_SIE, "lev")
        self.assertEqual(len(poster), 1)
        p = poster[0]
        self.assertEqual((p["fakturanummer"], p["belopp"], p["moms"], p["konto"], p["namn"]),
                         ("4471", d("1250"), d("250"), "6540", "Moln, Data & Drift AB"))

    def test_rensa_tar_bara_bort_importerade(self):
        poster, _, _ = fakturaimport.fran_sie(VISMA_SIE, "kund")
        fakturaimport.importera_kundfakturor(poster)
        egen = faktura(Kund.objects.first())
        egen.slutfor()
        self.assertEqual(fakturaimport.rensa_visma("kund"), 2)
        self.assertEqual(list(Faktura.objects.all()), [egen])

    def test_tom_sie(self):
        self.assertTrue(fakturaimport.ar_sie(b"#FLAGGA 0\n#SIETYP 4\n"))
        self.assertEqual(fakturaimport.fran_sie(b"#FLAGGA 0\n#SIETYP 4\n", "kund"), ([], [], 0))


class Bankfiler(TestCase):
    def test_csv_fran_banken(self):
        data = ("Kontoutdrag\n\nReskontradatum;Transaktionsdatum;Text;Belopp;Saldo\n"
                "2026-03-02;2026-03-02;BG 5050-1055 KUND AB 100;12 500,00;20 000,00\n"
                "2026-03-03;2026-03-03;Avgift;-25,00;19 975,00\n").encode("latin-1")
        poster = bankimport.las_kontoutdrag(data)
        self.assertEqual([(p["datum"], p["belopp"]) for p in poster],
                         [(datum("2026-03-02"), d("12500.00")), (datum("2026-03-03"), d("-25.00"))])
        self.assertEqual(poster[1]["saldo"], d("19975.00"))
        self.assertNotEqual(poster[0]["nyckel"], poster[1]["nyckel"])
        # samma fil två gånger ger samma nycklar (dubbletter känns igen)
        self.assertEqual([p["nyckel"] for p in bankimport.las_kontoutdrag(data)], [p["nyckel"] for p in poster])

    def test_csv_utan_rubrik_ger_tydligt_fel(self):
        with self.assertRaises(bankimport.BankimportFel):
            bankimport.las_kontoutdrag(b"a;b;c\n1;2;3\n")

    def test_camt053(self):
        xml = """<?xml version="1.0"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
<BkToCstmrStmt><Stmt><Acct><Id><IBAN>SE4550000000058398257466</IBAN></Id></Acct>
<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="SEK">1000.00</Amt><CdtDbtInd>CRDT</CdtDbtInd></Bal>
<Ntry><Amt Ccy="SEK">1250.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>BOOK</Sts><BookgDt><Dt>2026-03-05</Dt></BookgDt>
<AcctSvcrRef>X1</AcctSvcrRef><NtryDtls><TxDtls><Refs><EndToEndId>LF7</EndToEndId></Refs>
<RltdPties><Cdtr><Nm>Leverantör AB</Nm></Cdtr></RltdPties></TxDtls></NtryDtls></Ntry>
<Ntry><Amt Ccy="SEK">10.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts>PDNG</Sts><BookgDt><Dt>2026-03-06</Dt></BookgDt></Ntry>
</Stmt></BkToCstmrStmt></Document>""".encode()
        self.assertTrue(bankimport.ar_camt(xml))
        poster, iban = bankimport.las_camt(xml)
        self.assertEqual(iban, "SE4550000000058398257466")
        self.assertEqual(len(poster), 1, "preliminära poster (PDNG) hoppas över")
        p = poster[0]
        self.assertEqual((p["belopp"], p["end_to_end"], p["motpart"], p["saldo"]),
                         (d("-1250.00"), "LF7", "Leverantör AB", d("1000.00")))


class Avstamning(TestCase):
    def setUp(self):
        self.kund, self.lev = grunddata()

    def _post(self, belopp, text, **kw):
        return Bankpost.objects.create(datum=kw.pop("datum", datum("2026-04-02")), text=text, belopp=d(belopp),
                                       konto_id=1930, nyckel=text + belopp, **kw)

    def test_inbetalning_matchas_mot_kundfaktura(self):
        f = faktura(self.kund)
        f.slutfor()
        bok.bokfor_kundfaktura(f)
        post = self._post("12500", "BG INBET KUND AB FAKTURA 100")
        self.assertEqual(avstamning.matcha(post), "Kundfaktura 100 betald")
        f.refresh_from_db()
        self.assertEqual((f.status, str(f.betaldatum)), (Faktura.BETALD, "2026-04-02"))
        self.assertEqual(post.verifikation.serie, "I")

    def test_belopp_utan_fakturanummer_matchas_inte_automatiskt(self):
        f = faktura(self.kund)
        f.slutfor()
        self.assertIsNone(avstamning.matcha(self._post("12500", "INSÄTTNING")))

    def test_utbetalning_fran_betalfil_matchas_pa_end_to_end(self):
        lf = Leverantorsfaktura.objects.create(leverantor=self.lev, belopp=d("1250"), moms=d("250"), konto_id=5410,
                                               fakturadatum=datum("2026-03-01"))
        bok.bokfor_levfaktura(lf)
        post = self._post("-1250", "BG BETALNING", end_to_end=f"LF{lf.pk}")
        self.assertIn("betald", avstamning.matcha(post))
        lf.refresh_from_db()
        self.assertTrue(lf.betald)

    def test_regel_med_moms(self):
        Matchningsregel.objects.create(namn="Molntjänst", text_innehaller="molnet", riktning="ut", motkonto_id=5410,
                                       momssats=d("25"))
        post = self._post("-125", "KORTKÖP MOLNET.COM")
        self.assertEqual(avstamning.matcha(post), "Regel: Molntjänst")
        rader = {t.konto_id: t.belopp for t in post.verifikation.transaktioner.all()}
        self.assertEqual(rader, {1930: d("-125"), 5410: d("100"), 2641: d("25")})
        self.assertEqual(Matchningsregel.objects.get().traffar, 1)

    def test_befintlig_verifikation_kopplas(self):
        v = bok.bokfor(datum("2026-04-01"), "Skatt", [(1630, 500), (1930, -500)])
        post = self._post("-500", "SKATTEVERKET")
        self.assertEqual(avstamning.matcha(post), f"Kopplad till {v.vernr}")


class Betalfil(TestCase):
    def setUp(self):
        _, self.lev = grunddata()

    def test_ocr_kontroll(self):
        self.assertTrue(betalfil._ocr_giltig("17855"))
        self.assertFalse(betalfil._ocr_giltig("17856"))
        self.assertFalse(betalfil._ocr_giltig("1"))

    def test_pain001(self):
        lf = Leverantorsfaktura.objects.create(leverantor=self.lev, belopp=d("1250"), konto_id=5410, ocr="17855",
                                               fakturadatum=datum("2026-03-01"))
        xml, antal, total = betalfil.skapa([lf], Foretag.hamta(), datum("2026-03-30"))
        self.assertEqual((antal, total), (1, d("1250")))
        x = xml.decode()
        for bit in ("pain.001.001.03", "<MmbId>9900</MmbId>", "<Prtry>BGNR</Prtry>", f"<EndToEndId>LF{lf.pk}",
                    "<Ref>17855</Ref>", "<ReqdExctnDt>2026-03-30", "<CtrlSum>1250.00</CtrlSum>"):
            self.assertIn(bit, x)

    def test_kraver_iban_och_mottagarkonto(self):
        lf = Leverantorsfaktura.objects.create(leverantor=self.lev, belopp=d("1"), konto_id=5410)
        f = Foretag.hamta()
        f.iban = ""
        with self.assertRaises(betalfil.BetalfilFel):
            betalfil.skapa([lf], f)
        self.lev.bankgiro = ""
        self.lev.save()
        with self.assertRaisesMessage(betalfil.BetalfilFel, "saknar bankgiro"):
            betalfil.skapa([lf], Foretag.hamta())

