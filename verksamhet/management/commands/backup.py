"""Krypterade säkerhetskopior i OneDrive (databas, bilagor och årsarkiv)."""

import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from config import kryptering as k

DB_MONSTER = "konsultsystem_*.sqlite3" + k.ANDELSE


def bilagemapp():
    return settings.BACKUP_DIR / "bilagor-krypterade"


def gor_backup(behall=30):
    """Tar en krypterad kopia av databasen och speglar bilagorna. Returnerar sökvägen till kopian."""
    db = Path(settings.DATABASES["default"]["NAME"])
    if not db.exists():
        return None
    nyckel = k.hamta_nyckel()
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        kopia = Path(tmp) / "kopia.sqlite3"
        kalla, dest = sqlite3.connect(db), sqlite3.connect(kopia)
        try:
            kalla.backup(dest)
        finally:
            dest.close()
            kalla.close()
        mal = settings.BACKUP_DIR / f"konsultsystem_{datetime.now():%Y%m%d_%H%M%S}.sqlite3{k.ANDELSE}"
        k.skriv_krypterad(mal, kopia.read_bytes(), nyckel)
    _spegla_bilagor(nyckel)
    kryptera_gamla_kopior(nyckel)
    for g in sorted(settings.BACKUP_DIR.glob(DB_MONSTER))[:-behall]:
        g.unlink()
    return mal


def _spegla_bilagor(nyckel):
    """Krypterar nya bilagor till bilagor-krypterade/ (läggs bara till, raderas aldrig)."""
    kalla = Path(settings.MEDIA_ROOT)
    if not kalla.exists():
        return
    for f in kalla.rglob("*"):
        if f.is_file():
            mal = bilagemapp() / (str(f.relative_to(kalla)) + k.ANDELSE)
            if not mal.exists() or mal.stat().st_mtime < f.stat().st_mtime:
                k.skriv_krypterad(mal, f.read_bytes(), nyckel)


def kryptera_gamla_kopior(nyckel):
    """Okrypterade kopior från tidigare versioner krypteras och tas bort när krypteringen kontrollerats."""
    bas = settings.BACKUP_DIR
    antal = 0
    kandidater = [(f, f.with_name(f.name + k.ANDELSE)) for f in bas.glob("konsultsystem_*.sqlite3")]
    kandidater += [(f, f.with_name(f.name + k.ANDELSE)) for f in (bas / "arsarkiv").glob("*.zip")]
    gammal_bilagemapp = bas / "bilagor"
    if gammal_bilagemapp.exists():
        kandidater += [(f, bilagemapp() / (str(f.relative_to(gammal_bilagemapp)) + k.ANDELSE))
                       for f in gammal_bilagemapp.rglob("*") if f.is_file()]
    for kalla, mal in kandidater:
        data = kalla.read_bytes()
        if not mal.exists():
            k.skriv_krypterad(mal, data, nyckel)
        if k.dekryptera(mal.read_bytes(), nyckel) == data:
            kalla.unlink()
            antal += 1
    if gammal_bilagemapp.exists():
        for mapp in sorted(gammal_bilagemapp.rglob("*"), reverse=True):
            if mapp.is_dir() and not any(mapp.iterdir()):
                mapp.rmdir()
        if not any(gammal_bilagemapp.iterdir()):
            gammal_bilagemapp.rmdir()
    return antal


def senaste_kopia():
    kopior = sorted(settings.BACKUP_DIR.glob(DB_MONSTER)) if settings.BACKUP_DIR.exists() else []
    return kopior[-1] if kopior else None


def kontrollera_kopia(fil):
    """Dekrypterar kopian och kontrollerar att den är en hel databas. Returnerar antal verifikationer."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "kontroll.sqlite3"
        db.write_bytes(k.dekryptera(Path(fil).read_bytes()))
        con = sqlite3.connect(db)
        try:
            svar = con.execute("PRAGMA integrity_check").fetchone()[0]
            if svar != "ok":
                raise k.KrypteringsFel(f"Kopian är skadad: {svar}")
            return con.execute("SELECT COUNT(*) FROM bokforing_verifikation").fetchone()[0]
        finally:
            con.close()


class Command(BaseCommand):
    help = "Tar en krypterad kopia av databasen och bilagorna till backupmappen i OneDrive"

    def handle(self, *args, **opts):
        mal = gor_backup()
        self.stdout.write(f"Backup sparad: {mal}" if mal else "Ingen databas ännu.")
