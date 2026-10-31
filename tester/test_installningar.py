"""Var data och säkerhetskopior hamnar."""

import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from config.datadir import hitta_backupmapp


class Backupmapp(SimpleTestCase):
    def test_foretagets_onedrive_forst(self):
        with tempfile.TemporaryDirectory() as foretag, tempfile.TemporaryDirectory() as privat:
            with mock.patch.dict(os.environ, {"OneDriveCommercial": foretag, "OneDrive": privat}, clear=False):
                os.environ.pop("BITERO_BACKUP_DIR", None)
                self.assertEqual(hitta_backupmapp(Path("/kod")), Path(foretag) / "bitero-erp-backups")

    def test_egen_plats_gar_fore(self):
        with mock.patch.dict(os.environ, {"BITERO_BACKUP_DIR": "/annan/plats", "OneDrive": "/x"}):
            self.assertEqual(hitta_backupmapp(Path("/kod")), Path("/annan/plats"))

    def test_utan_onedrive_i_programmappen(self):
        rensat = {k: v for k, v in os.environ.items() if k not in ("OneDrive", "OneDriveCommercial", "BITERO_BACKUP_DIR")}
        with mock.patch.dict(os.environ, rensat, clear=True):
            self.assertEqual(hitta_backupmapp(Path("/kod")), Path("/kod/backups"))
