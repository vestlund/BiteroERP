"""Krypterade säkerhetskopior, extern kopia och kontrollerna vid start."""

import io
import os
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings

from bokforing import tjanster as bok
from config import kryptering as k
from verksamhet import halsokontroll as h
from verksamhet.management.commands import backup
from verksamhet.management.commands.extern_kopia import extern_kopia

from .grund import datum, grunddata


class TillfalligLagring(TestCase):
    """Datamapp, backupmapp och bilagor i en tillfällig mapp, och en liten databasfil att kopiera."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rot = Path(self.tmp.name)
        self.backupmapp = self.rot / "OneDrive" / "bitero-erp-backups"
        o = override_settings(DATA_DIR=self.rot / "data", BACKUP_DIR=self.backupmapp, MEDIA_ROOT=self.rot / "bilagor")
        o.enable()
        self.addCleanup(o.disable)
        (self.rot / "data").mkdir()
        self.db = self.rot / "data" / "konsultsystem.sqlite3"
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE bokforing_verifikation (id INTEGER PRIMARY KEY)")
        con.executemany("INSERT INTO bokforing_verifikation VALUES (?)", [(1,), (2,), (3,)])
        con.commit()
        con.close()
        p = mock.patch.dict(settings.DATABASES["default"], {"NAME": self.db})
        p.start()
        self.addCleanup(p.stop)


class Kryptering(TillfalligLagring):
    def test_kryptera_och_dekryptera(self):
        text = k.skapa_nyckel()
        self.assertEqual(len(text.split("-")), 13)
        data = os.urandom(5000)
        krypt = k.kryptera(data)
        self.assertNotIn(data[:100], krypt)
        self.assertEqual(k.dekryptera(krypt), data)
        # nyckeln kan skrivas in igen från lösenordshanteraren, med eller utan bindestreck och gemener
        self.assertEqual(k.text_till_nyckel(text.lower().replace("-", " ")), k.hamta_nyckel())

    def test_fel_nyckel_och_manipulerad_fil(self):
        k.skapa_nyckel()
        krypt = k.kryptera(b"hemligt")
        with self.assertRaisesMessage(k.KrypteringsFel, "annan nyckel"):
            k.dekryptera(krypt, os.urandom(32))
        andrad = krypt[:-1] + bytes([krypt[-1] ^ 1])
        with self.assertRaisesMessage(k.KrypteringsFel, "skadad"):
            k.dekryptera(andrad)
        with self.assertRaisesMessage(k.KrypteringsFel, "inte en krypterad"):
            k.dekryptera(b"vanlig fil")

    def test_utan_nyckel(self):
        with self.assertRaisesMessage(k.KrypteringsFel, "saknas"):
            backup.gor_backup()
        with self.assertRaisesMessage(k.KrypteringsFel, "redan"):
            k.skapa_nyckel(), k.skapa_nyckel()


class Sakerhetskopior(TillfalligLagring):
    def setUp(self):
        super().setUp()
        k.skapa_nyckel()

    def test_kopian_ar_krypterad_och_gar_att_aterstalla(self):
        (self.rot / "bilagor" / "2026").mkdir(parents=True)
        (self.rot / "bilagor" / "2026" / "kvitto.pdf").write_bytes(b"%PDF kvitto")
        fil = backup.gor_backup()
        self.assertTrue(fil.name.endswith(".sqlite3.krypterad"))
        self.assertNotIn(b"SQLite format", fil.read_bytes())
        self.assertEqual(backup.kontrollera_kopia(fil), 3)
        bilaga = self.backupmapp / "bilagor-krypterade" / "2026" / "kvitto.pdf.krypterad"
        self.assertEqual(k.dekryptera(bilaga.read_bytes()), b"%PDF kvitto")

        call_command("dekryptera", str(self.backupmapp / "bilagor-krypterade"), till=str(self.rot / "ut"),
                     stdout=io.StringIO())
        self.assertEqual((self.rot / "ut" / "2026" / "kvitto.pdf").read_bytes(), b"%PDF kvitto")
        call_command("dekryptera", str(fil), till=str(self.rot / "ut"), stdout=io.StringIO())
        self.assertEqual((self.rot / "ut" / fil.name.removesuffix(".krypterad")).read_bytes()[:15], b"SQLite format 3")
        with self.assertRaisesMessage(CommandError, "finns redan"):
            call_command("dekryptera", str(fil), till=str(self.rot / "ut"), stdout=io.StringIO())

    def test_gamla_okrypterade_kopior_krypteras(self):
        self.backupmapp.mkdir(parents=True)
        gammal = self.backupmapp / "konsultsystem_20260101_080000.sqlite3"
        gammal.write_bytes(self.db.read_bytes())
        (self.backupmapp / "bilagor" / "a").mkdir(parents=True)
        (self.backupmapp / "bilagor" / "a" / "x.jpg").write_bytes(b"bild")
        (self.backupmapp / "arsarkiv").mkdir()
        (self.backupmapp / "arsarkiv" / "arsarkiv_2025.zip").write_bytes(b"PK zip")
        backup.gor_backup()
        self.assertEqual(sorted(p.name for p in self.backupmapp.rglob("*") if p.is_file() and
                                not p.name.endswith(".krypterad")), [])
        self.assertFalse((self.backupmapp / "bilagor").exists())
        self.assertEqual(backup.kontrollera_kopia(gammal.with_name(gammal.name + ".krypterad")), 3)
        self.assertEqual(k.dekryptera((self.backupmapp / "bilagor-krypterade" / "a" / "x.jpg.krypterad").read_bytes()),
                         b"bild")

    def test_bara_de_30_senaste_sparas(self):
        self.backupmapp.mkdir(parents=True)
        for i in range(32):
            (self.backupmapp / f"konsultsystem_202601{i:02d}_000000.sqlite3.krypterad").write_bytes(b"x")
        backup.gor_backup()
        self.assertEqual(len(list(self.backupmapp.glob(backup.DB_MONSTER))), 30)

    def test_skadad_kopia_upptacks(self):
        fil = backup.gor_backup()
        fil.write_bytes(k.kryptera(b"inte en databas" * 100))
        a = h.sakerhetskopian()
        self.assertEqual((a.niva, a.rubrik), (h.FEL, "Säkerhetskopian går inte att läsa"))

    def test_extern_kopia(self):
        (self.rot / "bilagor").mkdir()
        (self.rot / "bilagor" / "k.png").write_bytes(b"png")
        usb = self.rot / "usb"
        with self.assertRaisesMessage(CommandError, "ansluten"):
            extern_kopia(usb)
        usb.mkdir()
        self.assertEqual(h.extern_kopia().rubrik, "Ingen kopia på extern disk")
        mal, db, antal = extern_kopia(usb)
        self.assertEqual(antal, 1)
        self.assertEqual(backup.kontrollera_kopia(mal / "databas" / db), 3)
        self.assertIsNone(h.extern_kopia())
        self.assertEqual(extern_kopia(usb)[2], 0)  # bara nya filer kopieras
        gammalt = (datetime.now() - timedelta(days=40)).isoformat()
        (self.rot / "data" / "senaste_externa_kopia.txt").write_text(gammalt)
        self.assertIn("40 dagar", h.extern_kopia().text)

    def test_backupnyckel_kommando(self):
        ut = io.StringIO()
        call_command("backupnyckel", stdout=ut)
        self.assertIn(k.nyckel_som_text(k.hamta_nyckel()), ut.getvalue())
        with self.assertRaisesMessage(CommandError, "annan nyckel"):
            call_command("backupnyckel", ange=k.nyckel_som_text(os.urandom(32)), stdout=io.StringIO())
        text = k.nyckel_som_text(k.hamta_nyckel())
        k.nyckelfil().unlink()
        call_command("backupnyckel", ange=text, stdout=io.StringIO())
        self.assertEqual(k.nyckel_som_text(k.hamta_nyckel()), text)


class Startkontroller(TillfalligLagring):
    def setUp(self):
        super().setUp()
        k.skapa_nyckel()
        grunddata()
        self.ver = bok.bokfor(datum("2026-03-01"), "Kontorsmaterial", [(5410, 100), (1930, -100)])

    def test_allt_i_ordning(self):
        backup.gor_backup()
        (self.rot / "data" / "senaste_externa_kopia.txt").write_text(datetime.now().isoformat())
        with mock.patch.object(h, "lokala_andringar", return_value=None):
            self.assertEqual(h.kor_alla(), [])
        self.assertEqual(h.senaste(), [])

    def test_obalans_och_bruten_historik(self):
        with connection.cursor() as c:
            c.execute("INSERT INTO bokforing_transaktion (verifikation_id, konto_id, belopp, text) "
                      "VALUES (%s, 1930, 5, '')", [self.ver.pk])
            c.execute("UPDATE logg_handelse SET objekt = 'ändrad' WHERE id = (SELECT MIN(id) FROM logg_handelse)")
        self.assertIn(self.ver.vernr, h.balans().text)
        self.assertEqual(h.behandlingshistoriken().rubrik, "Behandlingshistoriken är bruten")

    def test_resultatet_visas_pa_sidorna(self):
        with mock.patch.object(h, "lokala_andringar", return_value=None):
            res = h.kor_alla(backup_fel=RuntimeError("OneDrive-mappen saknas"))
        self.assertEqual([a.rubrik for a in res], ["Säkerhetskopian misslyckades", "Ingen kopia på extern disk"])
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_superuser("u", "u@x.se", "x"))
        sida = self.client.get("/").content.decode()
        self.assertIn("Säkerhetskopian misslyckades", sida)
        self.assertIn("OneDrive-mappen saknas", sida)

    def test_lokala_andringar(self):
        repo = self.rot / "repo"
        repo.mkdir()
        self.assertEqual(h.lokala_andringar(repo).rubrik, "Programmet körs inte från git")

        def git(*a):
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=repo, check=True,
                           capture_output=True)
        git("init", "-q")
        (repo / "a.py").write_text("x = 1\n")
        git("add", "a.py")
        git("commit", "-qm", "första")
        self.assertIsNone(h.lokala_andringar(repo))
        (repo / "a.py").write_text("x = 2\n")
        a = h.lokala_andringar(repo)
        self.assertEqual((a.niva, a.rubrik), (h.FEL, "Programfilerna är ändrade lokalt"))
        self.assertIn("a.py", a.text)


class Installningar(TestCase):
    def test_inloggad_en_vecka(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 7 * 24 * 3600)


class ForstaStart(TillfalligLagring):
    def kommando(self):
        from verksamhet.management.commands.starta import Command
        c = Command(stdout=io.StringIO())
        return c

    def test_ny_nyckel_visas_en_gang(self):
        c = self.kommando()
        with mock.patch("builtins.input", return_value=""):
            c.sakerstall_nyckel()
        self.assertIn(k.nyckel_som_text(k.hamta_nyckel()), c.stdout._out.getvalue())

    def test_nyckel_fragas_efter_om_kopior_finns(self):
        k.skapa_nyckel()
        backup.gor_backup()
        text = k.nyckel_som_text(k.hamta_nyckel())
        k.nyckelfil().unlink()
        c = self.kommando()
        with mock.patch("builtins.input", return_value=text):
            c.sakerstall_nyckel()
        self.assertEqual(k.nyckel_som_text(k.hamta_nyckel()), text)
        k.nyckelfil().unlink()
        with mock.patch("builtins.input", return_value=""), self.assertRaises(SystemExit):
            self.kommando().sakerstall_nyckel()
        self.assertFalse(k.finns_nyckel())
