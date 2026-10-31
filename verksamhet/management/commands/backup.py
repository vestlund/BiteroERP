import sqlite3
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand


def gor_backup(behall=30):
    db = settings.DATABASES["default"]["NAME"]
    if not db.exists():
        return None
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    mal = settings.BACKUP_DIR / f"konsultsystem_{datetime.now():%Y%m%d_%H%M%S}.sqlite3"
    kalla, dest = sqlite3.connect(db), sqlite3.connect(mal)
    try:
        kalla.backup(dest)
    finally:
        dest.close()
        kalla.close()
    _spegla_bilagor()
    gamla = sorted(settings.BACKUP_DIR.glob("konsultsystem_*.sqlite3"))[:-behall]
    for g in gamla:
        g.unlink()
    return mal


def _spegla_bilagor():
    """Kopierar nya bilagor till backups/bilagor/ (läggs bara till, raderas aldrig)."""
    import shutil

    kalla = settings.MEDIA_ROOT
    if not kalla.exists():
        return
    for f in kalla.rglob("*"):
        if f.is_file():
            mal = settings.BACKUP_DIR / "bilagor" / f.relative_to(kalla)
            if not mal.exists() or mal.stat().st_size != f.stat().st_size:
                mal.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, mal)


class Command(BaseCommand):
    help = "Tar en säker kopia av databasen till backupmappen i OneDrive (behåller de 30 senaste)"

    def handle(self, *args, **opts):
        mal = gor_backup()
        self.stdout.write(f"Backup sparad: {mal}" if mal else "Ingen databas ännu.")
