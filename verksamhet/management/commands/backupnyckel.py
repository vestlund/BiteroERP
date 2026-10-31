"""Visar backupnyckeln, eller sparar en nyckel från lösenordshanteraren på en ny dator."""

from django.core.management.base import BaseCommand, CommandError

from config import kryptering as k


class Command(BaseCommand):
    help = "Visar backupnyckeln (spara den i din lösenordshanterare) eller anger en sparad nyckel med --ange"

    def add_arguments(self, parser):
        parser.add_argument("--ange", metavar="NYCKEL", help="Nyckeln från lösenordshanteraren (ny dator)")

    def handle(self, *args, **opts):
        try:
            if opts["ange"]:
                if k.finns_nyckel() and k.hamta_nyckel() != k.text_till_nyckel(opts["ange"]):
                    raise CommandError(f"Det finns redan en annan nyckel i {k.nyckelfil()}. Ta bort filen först "
                                       "om du verkligen vill byta (äldre kopior kräver då den gamla nyckeln).")
                k.ange_nyckel(opts["ange"])
                self.stdout.write(f"Nyckeln sparad i {k.nyckelfil()}.")
                return
            if not k.finns_nyckel():
                k.skapa_nyckel()
            self.stdout.write("Backupnyckel (spara i lösenordshanteraren):\n\n    "
                              + k.nyckel_som_text(k.hamta_nyckel()) + "\n")
        except k.KrypteringsFel as e:
            raise CommandError(str(e)) from e
