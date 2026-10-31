"""Claude-kopplingen (MCP): verktygen för inkorg, sökning och bokföring av kvitton och leverantörsfakturor."""

import asyncio
import io
import json
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from fpdf import FPDF
from PIL import Image

from assistent import verktyg as v
from bokforing.models import Bilaga, Leverantorsfaktura
from logg.models import Handelse
from register.models import Konto

from .grund import grunddata


def kvittopdf():
    p = FPDF()
    p.add_page()
    p.set_font("Helvetica", size=11)
    for r in ["Leverantör AB", "Fakturanr: 77", "Fakturadatum: 2026-03-05", "Summa att betala 1 250,00",
              "Moms 25 % 250,00", "Bankgiro 5050-1055"]:
        p.cell(0, 7, r, new_x="LMARGIN", new_y="NEXT")
    return bytes(p.output())


class Verktyg(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inkorg = Path(self.tmp.name) / "inkorg"
        self.inkorg.mkdir()
        o = override_settings(INKORG_DIR=self.inkorg, MEDIA_ROOT=Path(self.tmp.name) / "media")
        o.enable()
        self.addCleanup(o.disable)
        self.kund, self.lev = grunddata()
        Konto.objects.update_or_create(nummer=5831, defaults={"namn": "Kost och logi", "aktiv": True})
        Image.new("RGB", (2400, 3200), "white").save(self.inkorg / "kvitto.jpg")
        (self.inkorg / "faktura.pdf").write_bytes(kvittopdf())
        (self.inkorg / "anteckning.txt").write_text("inget underlag")

    def test_inkorg(self):
        filer = {f["fil"]: f for f in v.inkorg()["filer"]}
        self.assertEqual(set(filer), {"kvitto.jpg", "faktura.pdf"})
        self.assertIsNone(filer["kvitto.jpg"]["redan_bokford"])

    def test_visa_bild_skalas_ned(self):
        text, bild, fmt = v.visa_underlag("kvitto.jpg")
        self.assertEqual(fmt, "jpeg")
        from io import BytesIO
        self.assertEqual(Image.open(BytesIO(bild)).size, (1200, 1600))

    def test_visa_pdf_ger_text_och_forslag(self):
        text, bild, _ = v.visa_underlag("faktura.pdf")
        self.assertIsNone(bild)
        self.assertIn("'belopp': '1250.00'", text)
        self.assertIn("Leverantör AB", text)

    def test_filnamn_kan_inte_ga_utanfor_inkorgen(self):
        with self.assertRaises(v.VerktygsFel):
            v.visa_underlag("../../etc/passwd")
        with self.assertRaises(v.VerktygsFel):
            v.visa_underlag("anteckning.txt")

    def test_registrera_kvitto(self):
        # via MCP-verktyget så att behandlingshistoriken får användaren Claude
        from assistent.server import registrera_kvitto
        res = registrera_kvitto("kvitto.jpg", "2026-10-30", "224,00", "24,00", 5831, motpart="Cafe Central",
                                beskrivning="Lunch")
        self.assertEqual(res["verifikation"], "L1")
        self.assertIn("5831 Kost och logi: debet 200.00", res["kontering"])
        self.assertIsNone(res["varning"])
        lf = Leverantorsfaktura.objects.get()
        self.assertEqual((lf.typ, lf.betaldatum.isoformat(), lf.betalkonto_id), ("kvitto", "2026-10-30", 1930))
        b = Bilaga.objects.get()
        self.assertEqual((b.levfaktura, b.verifikation.vernr), (lf, "L1"))
        self.assertFalse((self.inkorg / "kvitto.jpg").exists())
        self.assertTrue((self.inkorg / "Bokförda" / "2026-10" / "kvitto.jpg").exists())
        self.assertTrue(Handelse.objects.filter(anvandare="Claude", kalla="MCP registrera_kvitto").exists())

    def test_dubbletter_stoppas(self):
        v.registrera_kvitto("kvitto.jpg", "2026-10-30", "224", "24", 5831, motpart="Cafe Central")
        Image.new("RGB", (10, 10), "red").save(self.inkorg / "kvitto2.jpg")
        with self.assertRaisesMessage(v.VerktygsFel, "samma datum"):
            v.registrera_kvitto("kvitto2.jpg", "2026-10-30", "224", "24", 5831, motpart="cafe central")
        res = v.registrera_kvitto("kvitto2.jpg", "2026-10-30", "224", "24", 5831, motpart="Cafe Central",
                                  tillat_dubblett=True)
        self.assertEqual(res["verifikation"], "L2")
        # samma fil igen
        kopia = self.inkorg / "igen.jpg"
        kopia.write_bytes((self.inkorg / "Bokförda" / "2026-10" / "kvitto2.jpg").read_bytes())
        with self.assertRaisesMessage(v.VerktygsFel, "redan bokförd"):
            v.registrera_kvitto("igen.jpg", "2026-10-31", "1", "0", 5831, motpart="X")

    def test_validering(self):
        for args, fel in [(("kvitto.jpg", "2026-10-30", "100", "100", 5831), "Momsen"),
                          (("kvitto.jpg", "30/10", "100", "20", 5831), "datum"),
                          (("kvitto.jpg", "2026-10-30", "100", "20", 9999), "finns inte"),
                          ]:
            with self.subTest(args=args):
                with self.assertRaisesMessage(v.VerktygsFel, fel):
                    v.registrera_kvitto(*args, motpart="Cafe")
        with self.assertRaisesMessage(v.VerktygsFel, "motpart"):
            v.registrera_kvitto("kvitto.jpg", "2026-10-30", "100", "20", 5831)
        res = v.registrera_kvitto("kvitto.jpg", "2026-10-30", "100", "15", 5831, motpart="Cafe")
        self.assertIn("OBS: momsen", res["varning"])

    def test_privat_utlagg(self):
        Konto.objects.update_or_create(nummer=2893, defaults={"namn": "Skuld till närstående", "aktiv": True})
        res = v.registrera_kvitto("kvitto.jpg", "2026-10-30", "125", "25", 5831, motpart="Taxi", betalkonto=2893)
        self.assertTrue(any(r.startswith("2893") and "kredit 125.00" in r for r in res["kontering"]))

    def test_registrera_leverantorsfaktura(self):
        res = v.registrera_leverantorsfaktura("faktura.pdf", self.lev.nummer, "2026-03-05", "1250", "250", 5410,
                                              fakturanummer="77", ocr="17855")
        lf = Leverantorsfaktura.objects.get()
        self.assertEqual((lf.typ, lf.fakturanummer, str(lf.forfallodatum), lf.betaldatum), ("faktura", "77", "2026-04-04", None))
        self.assertTrue(any(r.startswith("2440") for r in res["kontering"]))
        with self.assertRaisesMessage(v.VerktygsFel, "kräver leverantör"):
            v.registrera_leverantorsfaktura("kvitto.jpg", None, "2026-03-05", "10", "2", 5410)

    def test_sok_och_ny_leverantor(self):
        self.assertEqual(v.sok_leverantor("5050-1055")[0]["nummer"], self.lev.nummer)
        self.assertEqual(v.sok_leverantor("leverantör")[0]["foreslaget_konto"], "5410 Förbrukningsinventarier")
        ny = v.ny_leverantor("Molnbolaget AB", orgnr="556677-8899", bankgiro="123-4567", standardkonto=6540
                             if Konto.objects.filter(pk=6540).exists() else None)
        self.assertEqual(ny["namn"], "Molnbolaget AB")
        with self.assertRaisesMessage(v.VerktygsFel, "finns redan"):
            v.ny_leverantor("Molnbolaget", orgnr="556677-8899")

    def test_lasverktyg(self):
        self.assertTrue(v.kontoplan("58"))
        self.assertIn("kundfakturor", v.obetalda())
        self.assertIn("resultat_i_ar", v.ekonomi())
        v.registrera_kvitto("kvitto.jpg", "2026-10-30", "224", "24", 5831, motpart="Cafe Central")
        self.assertEqual(v.tidigare_kvitton("cafe")[0]["konto"], "5831 Kost och logi")
        self.assertEqual(v.sok_verifikationer("Cafe")[0]["ver"], "L1")
        self.assertEqual(v.momsrapport("2026-10-01", "2026-10-31")["48"], "24.00")


class Server(TestCase):
    def test_verktyg_och_beskrivningar(self):
        from assistent.server import server
        verktyg = asyncio.run(server.list_tools())
        namn = {t.name for t in verktyg}
        self.assertTrue({"inkorg", "visa_underlag", "registrera_kvitto", "registrera_leverantorsfaktura",
                         "sok_leverantor", "kontoplan", "ny_leverantor"} <= namn)
        skriv = {t.name for t in verktyg if t.annotations and not t.annotations.read_only_hint}
        self.assertEqual(skriv, {"registrera_kvitto", "registrera_leverantorsfaktura", "ny_leverantor"})
        self.assertTrue(all(t.description for t in verktyg))

    def test_fel_blir_verktygsfel(self):
        from mcp.server.mcpserver.exceptions import ToolError

        from assistent.server import registrera_kvitto
        with tempfile.TemporaryDirectory() as tmp, override_settings(INKORG_DIR=Path(tmp)):
            with self.assertRaisesMessage(ToolError, "finns inte i inkorgen"):
                registrera_kvitto("saknas.jpg", "2026-01-01", "1", "0", 5410, motpart="x")


class Installation(TestCase):
    def test_konfig_slas_ihop(self):
        from django.core.management import call_command
        with tempfile.TemporaryDirectory() as tmp:
            fil = Path(tmp) / "claude_desktop_config.json"
            fil.write_text(json.dumps({"mcpServers": {"annan": {"command": "x"}}, "ovrigt": 1}))
            with override_settings(INKORG_DIR=Path(tmp) / "inkorg"):
                call_command("installera_claude", fil=str(fil), stdout=io.StringIO())
            k = json.loads(fil.read_text())
            self.assertEqual(set(k["mcpServers"]), {"annan", "bitero-erp"})
            self.assertEqual(k["mcpServers"]["bitero-erp"]["args"][-1], "mcp")
            self.assertEqual(k["ovrigt"], 1)
            self.assertTrue((Path(tmp) / "claude_desktop_config.json.bak").exists())
            call_command("installera_claude", fil=str(fil), ta_bort=True, stdout=io.StringIO())
            self.assertEqual(set(json.loads(fil.read_text())["mcpServers"]), {"annan"})
