"""Kopierar de krypterade säkerhetskopiorna till en extern disk (rekommenderas en gång i månaden)."""

import shutil
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from .backup import DB_MONSTER, gor_backup, kontrollera_kopia

UNDERMAPP = "bitero-erp-extern"


def extern_kopia(rot, behall=12):
    """Senaste databaskopian, alla bilagor och årsarkiv kopieras till <rot>/bitero-erp-extern/.

    Allt är krypterat. Databaskopiorna sparas med datum (de 12 senaste), bilagor och arkiv läggs bara till.
    """
    rot = Path(rot)
    if not rot.exists():
        raise CommandError(f"{rot} finns inte. Är disken ansluten?")
    mal = rot / UNDERMAPP
    db = gor_backup()
    if db is None:
        raise CommandError("Ingen databas att kopiera.")
    kontrollera_kopia(db)
    (mal / "databas").mkdir(parents=True, exist_ok=True)
    shutil.copy2(db, mal / "databas" / db.name)
    kontrollera_kopia(mal / "databas" / db.name)
    for gammal in sorted((mal / "databas").glob(DB_MONSTER))[:-behall]:
        gammal.unlink()
    antal = 0
    for undermapp in ("bilagor-krypterade", "arsarkiv"):
        kalla = settings.BACKUP_DIR / undermapp
        if not kalla.exists():
            continue
        for f in kalla.rglob("*"):
            ny = mal / undermapp / f.relative_to(kalla)
            if f.is_file() and (not ny.exists() or ny.stat().st_size != f.stat().st_size):
                ny.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, ny)
                antal += 1
    (Path(settings.DATA_DIR) / "senaste_externa_kopia.txt").write_text(
        datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return mal, db.name, antal


class Command(BaseCommand):
    help = "Kopierar krypterade säkerhetskopior till en extern disk, t.ex. extern_kopia E:\\"

    def add_arguments(self, parser):
        parser.add_argument("mapp", help="Den externa diskens enhet eller mapp, t.ex. E:\\")

    def handle(self, *args, **opts):
        from config.kryptering import KrypteringsFel
        try:
            mal, db, antal = extern_kopia(opts["mapp"])
        except KrypteringsFel as e:
            raise CommandError(str(e)) from e
        self.stdout.write(f"Klart: {db} och {antal} nya bilagor/arkiv kopierade till {mal}. "
                          "Kopian är kontrollerad. Koppla ur disken och förvara den skilt från datorn.")
