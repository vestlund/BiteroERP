"""Fakturor som PDF, e-postutkast, OCR och påminnelser med dröjsmålsränta."""

import email
import email.policy
import io
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from bokforing.betalfil import _ocr_giltig
from register.models import Foretag
from verksamhet import fakturadokument as fd
from verksamhet.models import Faktura

from .grund import anvandare, d, datum, faktura, grunddata


def pdftext(data):
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


class Ocr(TestCase):
    def test_samma_format_som_visma(self):
        self.assertEqual(fd.ocr_for(178), "17855")   # Vismas OCR för faktura 178
        self.assertEqual(fd.ocr_for(179), "17954")
        for n in (1, 42, 180, 99999):
            self.assertTrue(_ocr_giltig(fd.ocr_for(n)))

    def test_slutfor_satter_ocr(self):
        kund, _ = grunddata()
        f = faktura(kund)
        f.slutfor()
        self.assertEqual(f.ocr, fd.ocr_for(100))


class Drojsmalsranta(TestCase):
    def test_per_halvar(self):
        # 100 000 kr, 60 dagar, referensränta 2 % + 8 % = 10 %
        self.assertEqual(fd.drojsmalsranta(Decimal(100000), datum("2026-06-01"), datum("2026-07-31")),
                         (Decimal("1644"), 60))
        # över halvårsskifte 2025: 3 % + 8 % resp. 2 % + 8 %
        ranta, dagar = fd.drojsmalsranta(Decimal(36500), datum("2025-06-20"), datum("2025-07-10"))
        self.assertEqual(dagar, 20)
        self.assertEqual(ranta, Decimal(round(36500 * (Decimal("0.11") * 10 + Decimal("0.10") * 10) / 365)))
        self.assertEqual(fd.drojsmalsranta(Decimal(1000), datum("2026-06-01"), datum("2026-05-01")), (Decimal(0), 0))


class Dokument(TestCase):
    def setUp(self):
        self.kund, _ = grunddata()
        self.kund.adress1, self.kund.postnummer, self.kund.ort = "Gatan 1", "111 22", "Stockholm"
        self.kund.kontakt_epost, self.kund.kopia_epost = "anna@kund.se", "ekonomi@kund.se"
        self.kund.save()
        foretag = Foretag.hamta()
        foretag.bankgiro, foretag.epost, foretag.var_referens = "5050-1055", "info@test.se", "Per Persson"
        foretag.save()
        self.f = faktura(self.kund, timmar="8", pris="1250")
        self.f.er_referens = "Anna Andersson"
        self.f.save()
        self.f.slutfor()

    def test_pdf(self):
        t = pdftext(fd.faktura_pdf(self.f, Foretag.hamta()))
        for bit in ("FAKTURA", "Nr 100", "Kund AB", "Gatan 1", "Konsulttjänster", "12 500,00", "OCR",
                    self.f.ocr, "bankgiro 5050-1055", "Godkänd för F-skatt", "Testbolaget AB"):
            self.assertIn(bit, t)

    def test_eml(self):
        pdf = fd.faktura_pdf(self.f, Foretag.hamta())
        m = email.message_from_bytes(fd.faktura_eml(self.f, Foretag.hamta(), pdf), policy=email.policy.default)
        self.assertEqual(m["To"], "anna@kund.se")
        self.assertEqual(m["Cc"], "ekonomi@kund.se")
        self.assertEqual(m["X-Unsent"], "1")
        self.assertIn("Faktura 100", m["Subject"])
        delar = list(m.walk())
        text = next(p for p in delar if p.get_content_type() == "text/plain").get_content()
        self.assertTrue(text.startswith("Hej Anna,"))
        self.assertIn(f"ange OCR {self.f.ocr}", text)
        bilaga = next(p for p in delar if p.get_content_type() == "application/pdf")
        self.assertEqual(bilaga.get_filename(), "Faktura_100.pdf")
        self.assertTrue(bilaga.get_payload(decode=True).startswith(b"%PDF"))

    def test_paminnelse(self):
        pdf, ranta, dagar = fd.paminnelse_pdf(self.f, Foretag.hamta(), datum("2026-05-09"), Decimal(60))
        self.assertEqual(dagar, 30)
        t = pdftext(pdf)
        self.assertIn("PÅMINNELSE", t)
        self.assertIn("Påminnelseavgift", t)
        self.assertIn("12 560,00", t)

    def test_vyer(self):
        self.client.force_login(anvandare())
        r = self.client.get(reverse("faktura_utskrift", args=[self.f.pk]))
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(self.client.get(reverse("faktura_utskrift", args=[self.f.pk]) + "?html=1").status_code, 200)
        r = self.client.post(reverse("faktura_epost", args=[self.f.pk]))
        self.assertEqual(r["Content-Type"], "message/rfc822")
        self.f.refresh_from_db()
        self.assertIsNotNone(self.f.utskickad)
        r = self.client.post(reverse("faktura_paminnelse", args=[self.f.pk]), {"format": "pdf", "avgift": "60"})
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.f.refresh_from_db()
        self.assertEqual(self.f.paminnelser, 1)
        self.assertEqual(self.client.get(reverse("faktura_detalj", args=[self.f.pk])).status_code, 200)

    def test_utkast_kan_inte_mejlas(self):
        self.client.force_login(anvandare())
        utkast = faktura(self.kund)
        r = self.client.post(reverse("faktura_epost", args=[utkast.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Faktura.objects.get(pk=utkast.pk).utskickad, None)
