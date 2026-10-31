"""Lägger in Bitero ERP som MCP-server i Claude-appen på datorn (claude_desktop_config.json)."""

import json
import os
import shutil
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

NAMN = "bitero-erp"


def konfigfil():
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def serverpost():
    return {"command": sys.executable, "args": [str(settings.BASE_DIR / "manage.py"), "mcp"],
            "env": {"PYTHONIOENCODING": "utf-8"}}


class Command(BaseCommand):
    help = "Kopplar Bitero ERP till Claude-appen så att Claude kan bokföra kvitton från inkorgen."

    def add_arguments(self, parser):
        parser.add_argument("--fil", help="Sökväg till claude_desktop_config.json (annars standardplatsen)")
        parser.add_argument("--ta-bort", action="store_true", help="Ta bort kopplingen")

    def handle(self, *args, **opts):
        fil = Path(opts["fil"]) if opts["fil"] else konfigfil()
        fil.parent.mkdir(parents=True, exist_ok=True)
        konfig = {}
        if fil.exists():
            try:
                konfig = json.loads(fil.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError as e:
                raise CommandError(f"{fil} är inte giltig JSON ({e}). Rätta eller ta bort filen och försök igen.")
            shutil.copy2(fil, fil.with_suffix(".json.bak"))
        servrar = konfig.setdefault("mcpServers", {})
        if opts["ta_bort"]:
            servrar.pop(NAMN, None)
            self.stdout.write("Kopplingen till Bitero ERP är borttagen.")
        else:
            servrar[NAMN] = serverpost()
            settings.INKORG_DIR.mkdir(parents=True, exist_ok=True)
            self.stdout.write(self.style.SUCCESS("Bitero ERP är kopplat till Claude-appen."))
            self.stdout.write(f"  Inställningar: {fil}")
            self.stdout.write(f"  Inkorg för kvitton: {settings.INKORG_DIR}")
            self.stdout.write("Avsluta Claude-appen helt (även i aktivitetsfältet) och starta den igen.")
        fil.write_text(json.dumps(konfig, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
