"""Avläsning av leverantörsfakturor och kvitton i PDF."""

from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from fpdf import FPDF

from bokforing import kvitto
from register.models import Leverantor

from .grund import anvandare, grunddata


def pdf(*rader):
    p = FPDF()
    p.add_page()
    p.set_font("Helvetica", size=11)
    for r in rader:
        p.cell(0, 7, r, new_x="LMARGIN", new_y="NEXT")
    return bytes(p.output())


FAKTURA = pdf("Molnbolaget AB", "Org.nr 556677-8899", "FAKTURA", "Fakturanr: A-1001", "Fakturadatum: 2026-03-05",
              "Förfallodatum: 2026-04-04", "Licens mars        1 000,00", "Summa exkl. moms   1 000,00",
              "Moms 25 %          250,00", "Summa att betala   1 250,00 kr", "Bankgiro: 5050-1055",
              "OCR: 17855")
KVITTO = pdf("CAFE CENTRAL", "Kvitto nr 4711", "05.03.2026 12:14", "Lunch x2        224,00",
             "TOTALT SEK        224,00", "Varav moms 12%    24,00")


class Tal(SimpleTestCase):
    def test_format(self):
        self.assertEqual(kvitto.tal("1 234,50"), Decimal("1234.50"))
        self.assertEqual(kvitto.tal("1.234,50"), Decimal("1234.50"))
        self.assertEqual(kvitto.tal("1,234.50"), Decimal("1234.50"))
        self.assertEqual(kvitto.tal("1234"), Decimal("1234"))
        self.assertEqual(kvitto.tal("1 000"), Decimal("1000"))


class Tolkning(TestCase):
    def test_leverantorsfaktura(self):
        _, lev = grunddata()
        lev.orgnr = "556677-8899"
        lev.save()
        r = kvitto.tolka(FAKTURA, Leverantor.objects.all(), "556000-0000")
        self.assertEqual(str(r["fakturadatum"]), "2026-03-05")
        self.assertEqual(str(r["forfallodatum"]), "2026-04-04")
        self.assertEqual(r["fakturanummer"], "A-1001")
        self.assertEqual((r["belopp"], r["moms"], r["momssats"]), (Decimal("1250.00"), Decimal("250.00"), 25))
        self.assertEqual(r["ocr"], "17855")
        self.assertEqual(r["bankgiro"], "5050-1055")
        self.assertEqual(r["leverantor"], lev)

    def test_leverantor_pa_bankgiro_och_namn(self):
        _, lev = grunddata()  # bankgiro 5050-1055 i grunddata
        self.assertEqual(kvitto.tolka(FAKTURA, Leverantor.objects.all())["leverantor"], lev)
        cafe = Leverantor.objects.create(nummer=9, namn="Cafe Central")
        self.assertEqual(kvitto.tolka(KVITTO, Leverantor.objects.all())["leverantor"], cafe)

    def test_kvitto(self):
        r = kvitto.tolka(KVITTO, [])
        self.assertEqual(str(r["fakturadatum"]), "2026-03-05")
        self.assertEqual((r["belopp"], r["moms"], r["momssats"]), (Decimal("224.00"), Decimal("24.00"), 12))
        self.assertEqual(r["fakturanummer"], "4711")

    def test_utan_text(self):
        p = FPDF()
        p.add_page()
        with self.assertRaisesMessage(kvitto.TolkningsFel, "ingen läsbar text"):
            kvitto.tolka(bytes(p.output()), [])
        with self.assertRaises(kvitto.TolkningsFel):
            kvitto.tolka(b"inte en pdf", [])

    def test_vy(self):
        grunddata()
        self.client.force_login(anvandare())
        from django.core.files.uploadedfile import SimpleUploadedFile
        r = self.client.post(reverse("levfaktura_tolka"), {"fil": SimpleUploadedFile("f.pdf", FAKTURA)})
        data = r.json()
        self.assertEqual((data["belopp"], data["moms"], data["momssats"]), ("1250,00", "250,00", "25"))
        self.assertTrue(data["leverantor"])
        r = self.client.post(reverse("levfaktura_tolka"), {"fil": SimpleUploadedFile("k.jpg", b"x")})
        self.assertIn("Bara PDF", r.json()["fel"])
        self.assertEqual(self.client.get(reverse("levfaktura_ny")).status_code, 200)
