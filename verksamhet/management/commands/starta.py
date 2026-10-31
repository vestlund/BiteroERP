"""Startar systemet: backup, migrering, tester efter uppdatering, och webbservern (waitress)."""

import threading
import webbrowser

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand

from config.version import VERSION
from logg.sparning import har_andrats, registrera_systemversion
from register.models import Kund

from .backup import gor_backup

ADRESS = "127.0.0.1"
PORT = "8000"


def kor_tester():
    """Kör testsviten i en egen process (testdatabasen ligger i minnet, din databas rörs inte)."""
    import re
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "manage.py", "test", "tester", "--noinput"], cwd=settings.BASE_DIR,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    ut = r.stdout + r.stderr
    antal = re.search(r"Ran (\d+) test", ut)
    n = antal.group(1) if antal else "?"
    if r.returncode == 0:
        return f"{n} tester OK"
    (settings.DATA_DIR / "senaste_testfel.txt").write_text(ut, encoding="utf-8")
    fel = re.search(r"FAILED \((.*?)\)", ut)
    return f"testerna misslyckades ({fel.group(1) if fel else 'fel vid körning'} av {n}), " \
           f"detaljer i {settings.DATA_DIR / 'senaste_testfel.txt'}"


class Command(BaseCommand):
    help = "Startar konsultsystemet på http://127.0.0.1:8000"

    def add_arguments(self, parser):
        parser.add_argument("--port", default=PORT)
        parser.add_argument("--ingen-webblasare", action="store_true")

    def handle(self, *args, **opts):
        backup = gor_backup()
        if backup:
            self.stdout.write(f"Backup: {backup}")
        call_command("migrate", verbosity=0)
        sv = None
        if har_andrats(settings.BASE_DIR):
            self.stdout.write("Programmet har uppdaterats – kör de automatiska testerna ...")
            tester = kor_tester()
            if not tester.endswith("OK"):
                self.stdout.write(self.style.ERROR(
                    f"\n  VARNING: {tester}.\n  Bokför inte förrän felet är rättat. Testerna körs igen vid nästa start.\n"))
                if input("  Starta ändå? (j/N) ").strip().lower() != "j":
                    return
            else:
                self.stdout.write(self.style.SUCCESS(f"  {tester}"))
                sv = registrera_systemversion(settings.BASE_DIR, VERSION, tester)
        if sv:
            self.stdout.write(f"Ny systemversion {VERSION} registrerad i behandlingshistoriken"
                              f" ({sv.antal_andringar} ändrade filer).")

        if not Kund.objects.exists() and any(settings.IMPORT_DIR.glob("*_Export*.csv")):
            self.stdout.write("Första start – importerar register från mappen import/ ...")
            call_command("importera_visma")

        if not get_user_model().objects.filter(is_superuser=True).exists():
            self.stdout.write(self.style.WARNING("\nSkapa din inloggning (används bara lokalt):"))
            call_command("createsuperuser")

        url = f"http://{ADRESS}:{opts['port']}/"
        self.stdout.write(self.style.SUCCESS(f"\nKonsultsystemet körs på {url}  –  stäng med Ctrl+C\n"))
        if not opts["ingen_webblasare"]:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        if settings.DEBUG:
            self.stdout.write(self.style.WARNING("Utvecklingsläge (BITERO_DEBUG=1): Djangos utvecklingsserver."))
            call_command("runserver", f"{ADRESS}:{opts['port']}", use_reloader=False)
            return
        from django.core.wsgi import get_wsgi_application
        from waitress import serve
        try:
            serve(get_wsgi_application(), host=ADRESS, port=int(opts["port"]), threads=8, ident="Bitero ERP")
        except KeyboardInterrupt:
            self.stdout.write("Stoppat.")
