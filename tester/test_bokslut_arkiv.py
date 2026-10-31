"""Årsarkivet."""

import hashlib
import io
import tempfile
import zipfile
from pathlib import Path

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from bokforing import arsarkiv, sie
from bokforing import tjanster as bok
from bokforing.models import Bilaga, IngaendeBalans
from register.models import Foretag

from .grund import anvandare, d, datum, faktura, grunddata


class Arsarkiv(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        installningar = override_settings(MEDIA_ROOT=self.tmp.name, BACKUP_DIR=Path(self.tmp.name) / "bk")
        installningar.enable()
        self.addCleanup(installningar.disable)

        self.kund, _ = grunddata()
        IngaendeBalans.objects.create(ar=2026, konto_id=1930, belopp=d("1000"))
        IngaendeBalans.objects.create(ar=2026, konto_id=2440, belopp=d("-1000"))
        f = faktura(self.kund)
        f.slutfor()
        self.ver = bok.bokfor_kundfaktura(f)
        bok.bokfor(datum("2026-05-01"), "Bankavgift", [(6570, d("50")), (1930, d("-50"))])
        bok.bokfor(datum("2025-05-01"), "Förra året", [(6570, d("1")), (1930, d("-1"))])
        b = Bilaga(verifikation=self.ver, namn="underlag.pdf")
        b.fil.save("underlag.pdf", ContentFile(b"%PDF-1.4 test"), save=True)

    def test_innehall(self):
        namn, data, info = arsarkiv.skapa(2026)
        z = zipfile.ZipFile(io.BytesIO(data))
        filer = set(z.namelist())
        for f in ("SIE4_2026.se", "verifikationslista.pdf", "huvudbok.pdf", "resultat_och_balans.pdf",
                  "momsrapporter.pdf", "behandlingshistorik.csv", "systemdokumentation.html", "LASMIG.txt"):
            self.assertIn(f, filer)
        self.assertEqual(info["bilagor"], 1)
        self.assertTrue(any(n.startswith("bilagor/F1_") for n in filer))
        self.assertTrue(z.read("verifikationslista.pdf").startswith(b"%PDF"))
        self.assertTrue(info["kedjan_ok"])
        rad = next(r for r in z.read("LASMIG.txt").decode().splitlines() if r.startswith("huvudbok.pdf;"))
        self.assertEqual(rad.split("; ")[2], hashlib.sha256(z.read("huvudbok.pdf")).hexdigest())

    def test_sie_har_bara_arets_verifikationer_och_balanser(self):
        data = arsarkiv.sie_ar(2026, Foretag.hamta())
        vers, _ = sie.las_verifikationer(data)
        self.assertEqual(sorted(v["text"] for v in vers), sorted([self.ver.text, "Bankavgift"]))
        self.assertIn(("F", "1"), [(v["serie"], v["nummer"]) for v in vers])
        text = data.decode("cp437")
        self.assertIn("#RAR 0 20260101 20261231", text)
        self.assertIn("#IB 0 1930 1000.00", text)
        self.assertIn("#UB 0 1930 950.00", text)
        self.assertIn("#RES 0 3041 -10000.00", text)

    def test_sidan_och_nedladdning(self):
        self.client.force_login(anvandare())
        self.assertEqual(self.client.get(reverse("arsarkiv")).status_code, 200)
        r = self.client.post(reverse("arsarkiv"), {"ar": "2026"})
        self.assertEqual(r["Content-Type"], "application/zip")
        self.assertEqual(len(list((Path(self.tmp.name) / "bk" / "arsarkiv").glob("*.zip"))), 1)
        self.assertEqual(self.client.post(reverse("arsarkiv"), {"ar": "1999"}).status_code, 404)
