"""Läser krypterade säkerhetskopior: en fil eller en hel mapp (t.ex. bilagor-krypterade)."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from config import kryptering as k


class Command(BaseCommand):
    help = "Dekrypterar en säkerhetskopia (fil eller mapp) till en ny plats. Originalet rörs inte."

    def add_arguments(self, parser):
        parser.add_argument("kalla", help="Krypterad fil eller mapp")
        parser.add_argument("--till", help="Målmapp (standard: bitero-erp-data/dekrypterat)")
        parser.add_argument("--nyckel", help="Nyckel från lösenordshanteraren om den inte finns på datorn")

    def handle(self, *args, **opts):
        kalla = Path(opts["kalla"])
        till = Path(opts["till"] or Path(settings.DATA_DIR) / "dekrypterat")
        try:
            nyckel = k.text_till_nyckel(opts["nyckel"]) if opts["nyckel"] else k.hamta_nyckel()
        except k.KrypteringsFel as e:
            raise CommandError(str(e)) from e
        if kalla.is_dir():
            filer = [(f, till / f.relative_to(kalla)) for f in kalla.rglob("*" + k.ANDELSE)]
        elif kalla.is_file():
            filer = [(kalla, till / kalla.name)]
        else:
            raise CommandError(f"{kalla} finns inte.")
        for f, mal in filer:
            mal = mal.with_name(mal.name.removesuffix(k.ANDELSE))
            if mal.exists():
                raise CommandError(f"{mal} finns redan. Välj en annan målmapp med --till.")
            try:
                data = k.dekryptera(f.read_bytes(), nyckel)
            except k.KrypteringsFel as e:
                raise CommandError(f"{f}: {e}") from e
            mal.parent.mkdir(parents=True, exist_ok=True)
            mal.write_bytes(data)
        self.stdout.write(f"{len(filer)} fil(er) dekrypterade till {till}")
        if any(m.name.startswith("konsultsystem_") for _, m in filer):
            self.stdout.write("Återställa databasen: stäng programmet och kopiera filen till "
                              f"{settings.DATABASES['default']['NAME']} (ta bort -wal/-shm-filer bredvid).")
